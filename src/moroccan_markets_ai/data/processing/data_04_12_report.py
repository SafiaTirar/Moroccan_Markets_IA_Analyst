"""Consolide les livrables techniques et financiers de DATA-04.12."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.document_integrator import file_hash, read_json
from moroccan_markets_ai.data.processing.corpus_batch_runner import (
    MANIFEST_FILE,
    REPORT_FILE,
    WAREHOUSE_FILE,
    protected_state,
)
from moroccan_markets_ai.data.processing.financial_multisector_report import (
    _atomic_csv,
    build_coverage_matrix,
    verified_dataset,
)
from moroccan_markets_ai.data.processing.financial_reference_validation import (
    _atomic_json,
    _atomic_jsonl,
    _load_jsonl,
    run_reference_validation,
)
from moroccan_markets_ai.data.processing.financial_validation_extension import (
    DEFAULT_REGISTRY,
    validate_indicator_registry,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_12"
DEFAULT_REFERENCES = QUALITY_ROOT / "references/data_04_12_references.json"
DEFAULT_REVIEWS = QUALITY_ROOT / "review_dossiers/review_dossiers.json"
DEFAULT_PRIOR_COVERAGE = (
    PROJECT_ROOT / "data/quality/ammc/data_04_11/financial_coverage_matrix.csv"
)
DEFAULT_PRIOR_VERIFIED = (
    PROJECT_ROOT
    / "data/quality/ammc/data_04_11/verified_financial_observations.csv"
)
DEFAULT_INITIAL_EXECUTION = QUALITY_ROOT / "data_04_12_initial_execution.json"
SCHEMA_VERSION = "1.0"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def merge_coverage(
    prior: list[dict[str, Any]], extension: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    fields = ("company_id", "exercise_year", "accounting_perimeter", "indicator")
    merged = {
        tuple(str(row[field]) for field in fields): row for row in prior
    }
    for row in extension:
        merged[tuple(str(row[field]) for field in fields)] = row
    return sorted(
        merged.values(),
        key=lambda row: tuple(str(row[field]) for field in fields),
    )


def strict_verified_export(reference_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = verified_dataset(reference_report)
    if any(row["verification_status"] != "VERIFIED" for row in rows):
        raise ValueError("Une observation non VERIFIED a atteint l'export downstream")
    return rows


def _candidate_records(reference_report: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for document in reference_report["documents"]:
        report = read_json(Path(document["quality_report"]))
        for record in _load_jsonl(Path(report["outputs"]["annotations"])):
            records.append(
                {
                    "company_id": document["company_id"],
                    "exercise_year": document["exercise_year"],
                    **record,
                }
            )
    return records


def _coverage_summary(
    rows: list[dict[str, Any]], verified_observations: int
) -> dict[str, Any]:
    verified_rows = [row for row in rows if row["status"] == "VERIFIED"]
    return {
        "matrix_rows": len(rows),
        "by_status": dict(Counter(row["status"] for row in rows)),
        "verified_indicator_cells": len(verified_rows),
        "verified_observations": verified_observations,
        "companies_with_verified": len(
            {row["company_id"] for row in verified_rows}
        ),
        "company_years_with_verified": len(
            {(row["company_id"], str(row["exercise_year"])) for row in verified_rows}
        ),
    }


def run_report(
    references: Path = DEFAULT_REFERENCES,
    reviews: Path = DEFAULT_REVIEWS,
    output_root: Path = QUALITY_ROOT,
    registry_path: Path = DEFAULT_REGISTRY,
    prior_coverage_path: Path = DEFAULT_PRIOR_COVERAGE,
    prior_verified_path: Path = DEFAULT_PRIOR_VERIFIED,
    initial_execution_path: Path = DEFAULT_INITIAL_EXECUTION,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    technical = read_json(output_root / REPORT_FILE.name)
    initial_execution = read_json(Path(initial_execution_path))
    initial_by_key = {
        (item["company_id"], int(item["exercise_year"])): item
        for item in initial_execution["documents"]
    }
    reference_report = run_reference_validation(
        Path(references), output_root / "reference_validation"
    )
    registry = read_json(Path(registry_path))
    validate_indicator_registry(registry)
    prior_coverage = _read_csv(Path(prior_coverage_path))
    extension_coverage = build_coverage_matrix(reference_report, registry)
    combined_coverage = merge_coverage(prior_coverage, extension_coverage)
    verified = strict_verified_export(reference_report)
    candidates = _candidate_records(reference_report)
    review_payload = read_json(Path(reviews))

    verified_csv = output_root / "verified_financial_observations.csv"
    verified_jsonl = output_root / "verified_financial_observations.jsonl"
    candidate_jsonl = output_root / "candidate_observations.jsonl"
    extension_coverage_path = output_root / "financial_coverage_extension.csv"
    combined_coverage_path = output_root / "financial_coverage_after.csv"
    _atomic_csv(verified_csv, verified)
    _atomic_jsonl(verified_jsonl, verified)
    _atomic_jsonl(candidate_jsonl, candidates)
    _atomic_csv(extension_coverage_path, extension_coverage)
    _atomic_csv(combined_coverage_path, combined_coverage)

    status_counts = Counter(item["status"] for item in candidates)
    prior_verified_count = len(_read_csv(Path(prior_verified_path)))
    before = _coverage_summary(prior_coverage, prior_verified_count)
    after = _coverage_summary(
        combined_coverage, prior_verified_count + len(verified)
    )
    current_protected = protected_state()
    protected_unchanged = current_protected == technical["protected_state"]
    manifest_unchanged = (
        file_hash(MANIFEST_FILE) == technical["manifest_sha256_after"]
    )
    pdf_integrity = all(
        file_hash(Path(document["source_pdf"])) == document["source_pdf_sha256"]
        for document in reference_report["documents"]
    )
    document_failures = [
        item
        for item in technical["results"]
        if item.get("pipeline_status") == "FAILED"
    ]
    recommendation = (
        "NO-GO"
        if document_failures
        or not protected_unchanged
        or not manifest_unchanged
        or not pdf_integrity
        else "GO AVEC RÉSERVES"
        if any(
            item.get("pipeline_status") == "PARTIAL"
            for item in technical["results"]
        )
        else "GO"
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.12",
        "recommendation": recommendation,
        "processing": {
            "pdf_processed": technical["processed_document_count"],
            "authorized_documents": technical["authorized_documents"],
            "sequential": True,
            "max_parallel_documents": 1,
            "pages_processed": technical["totals"]["pages"],
            "initial_elapsed_seconds": initial_execution["totals"][
                "elapsed_seconds"
            ],
            "last_resume_elapsed_seconds": technical["totals"][
                "elapsed_seconds"
            ],
            "peak_rss_bytes": initial_execution["totals"]["peak_rss_bytes"],
            "initial_measurements": initial_execution["documents"],
            "documents": [
                {
                    "company_id": item["company_id"],
                    "exercise_year": item["exercise_year"],
                    "status": item["pipeline_status"],
                    "initial_elapsed_seconds": initial_by_key[
                        (item["company_id"], int(item["exercise_year"]))
                    ]["elapsed_seconds"],
                    "last_resume_elapsed_seconds": item["elapsed_seconds"],
                    "peak_rss_bytes": initial_by_key[
                        (item["company_id"], int(item["exercise_year"]))
                    ]["peak_rss_bytes"],
                    "stages": item["stages"],
                    "metrics": item["metrics"],
                    "reporting_scope": item["reporting_scope"],
                    "error": item["error"],
                }
                for item in technical["results"]
            ],
        },
        "tables": {
            "docling_detected": technical["totals"]["docling_tables"],
            "docling_non_empty": sum(
                item["metrics"]["docling_tables_non_empty"]
                for item in technical["results"]
            ),
            "ocr_detected": technical["totals"]["ocr_tables"],
            "ocr_non_empty": sum(
                item["metrics"]["ocr_tables_non_empty"]
                for item in technical["results"]
            ),
            "integrated_active": sum(
                item["metrics"]["integrated_active_tables"]
                for item in technical["results"]
            ),
        },
        "quality_gate": {
            "candidate_values": len(candidates),
            "reliable_references": reference_report["summary"][
                "reliable_references"
            ],
            "new_verified": len(verified),
            "candidate_statuses": {
                status: status_counts[status]
                for status in (
                    "VERIFIED",
                    "REVIEW_REQUIRED",
                    "REJECTED",
                    "NOT_CHECKED",
                )
            },
            "manual_review_dossiers": len(review_payload["dossiers"]),
            "downstream_allowed_statuses": ["VERIFIED"],
            "non_verified_leak_count": sum(
                row["verification_status"] != "VERIFIED" for row in verified
            ),
        },
        "coverage": {"before": before, "after": after},
        "integrity": {
            "manifest_unchanged": manifest_unchanged,
            "selected_pdfs_unchanged": pdf_integrity,
            "duckdb_and_streamlit_unchanged": protected_unchanged,
            "warehouse_sha256": (
                file_hash(WAREHOUSE_FILE) if WAREHOUSE_FILE.is_file() else None
            ),
        },
        "incidents": [
            "Les trois documents restent PARTIAL: ce statut documentaire ne remet pas en cause les neuf cellules comparées directement aux PDF.",
            "Wafa Assurance et Auto Hall n'ont produit aucune table OCR exploitable sur les pages ciblées; les tables Docling retenues restent traçables.",
            "Trois cellules prioritaires sont conservées en revue manuelle pour unité absente ou cellule période/valeur fusionnée.",
        ],
        "next_step": (
            "Mission distincte d'ingestion incrémentale des neuf observations VERIFIED, "
            "puis nouvelle extension AMMC contrôlée; ne pas relâcher le Quality Gate."
        ),
        "outputs": {
            "selection": technical["selection_file"],
            "technical_report": str((output_root / REPORT_FILE.name).resolve()),
            "candidate_observations": str(candidate_jsonl),
            "references": str(Path(references).resolve()),
            "reference_concordance": reference_report["outputs"]["observations"],
            "review_dossiers": str(Path(reviews).resolve()),
            "verified_csv": str(verified_csv),
            "verified_jsonl": str(verified_jsonl),
            "coverage_extension": str(extension_coverage_path),
            "coverage_after": str(combined_coverage_path),
        },
        "duckdb_ingestion_performed": False,
        "remaining_corpus_processed": False,
    }
    _atomic_json(output_root / "data_04_12_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--output-root", type=Path, default=QUALITY_ROOT)
    args = parser.parse_args()
    report = run_report(args.references, args.reviews, args.output_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
