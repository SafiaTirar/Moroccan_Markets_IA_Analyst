"""Consolide les livrables documentaires et financiers de DATA-04.13."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.document_integrator import file_hash, read_json
from moroccan_markets_ai.data.processing.corpus_batch_runner import (
    MANIFEST_FILE,
    WAREHOUSE_FILE,
    protected_state,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
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

QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_13"
DEFAULT_REFERENCES = QUALITY_ROOT / "references/data_04_13_references.json"
DEFAULT_TECHNICAL = QUALITY_ROOT / "data_04_13_technical_report.json"
DEFAULT_PRIOR_REFERENCES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json"
)
DEFAULT_PRIOR_VERIFIED = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/verified_financial_observations.csv"
)
DEFAULT_PRIOR_COVERAGE = (
    PROJECT_ROOT / "data/quality/ammc/data_04_12/financial_coverage_after.csv"
)
SCHEMA_VERSION = "1.0"
QUALITY_STATUSES = ("VERIFIED", "REVIEW_REQUIRED", "REJECTED", "NOT_CHECKED")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def strict_verified_export(reference_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = verified_dataset(reference_report)
    if any(
        row["verification_status"] != "VERIFIED" for row in rows
    ) or any(not item["downstream_eligible"] for item in reference_report["concordance"] if item["verification_status"] == "VERIFIED"):
        raise ValueError("Export downstream incohérent avec le statut VERIFIED")
    return rows


def add_version_metadata(
    rows: list[dict[str, Any]], specification: dict[str, Any]
) -> list[dict[str, Any]]:
    references = {
        observation["reference_id"]: (document, observation)
        for document in specification["documents"]
        for observation in document["observations"]
    }
    if len(references) != sum(
        len(document["observations"]) for document in specification["documents"]
    ):
        raise ValueError("Identifiants de références dupliqués")
    enriched = []
    for row in rows:
        document, observation = references[row["verification_reference_id"]]
        years = re.findall(r"(?:19|20)\d{2}", str(observation["period"]))
        if len(set(years)) != 1:
            raise ValueError("Période financière non déterministe")
        observation_year = int(years[0])
        report_year = int(document["exercise_year"])
        enriched.append(
            {
                **row,
                "report_exercise_year": report_year,
                "observation_year": observation_year,
                "period_role": (
                    "CURRENT" if observation_year == report_year else "COMPARATIVE"
                ),
                "source_label": observation["source_label"],
            }
        )
    return enriched


def merge_coverage(
    prior: list[dict[str, Any]], extension: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    keys = ("company_id", "exercise_year", "accounting_perimeter", "indicator")
    merged = {tuple(str(row[key]) for key in keys): row for row in prior}
    for row in extension:
        merged[tuple(str(row[key]) for key in keys)] = row
    return sorted(merged.values(), key=lambda row: tuple(str(row[key]) for key in keys))


def candidate_records(reference_report: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for document in reference_report["documents"]:
        report = read_json(Path(document["quality_report"]))
        records.extend(
            {
                "company_id": document["company_id"],
                "exercise_year": document["exercise_year"],
                "source_pdf": document["source_pdf"],
                **item,
            }
            for item in _load_jsonl(Path(report["outputs"]["annotations"]))
        )
    return records


def build_review_dossiers(
    candidates: list[dict[str, Any]], document_count: int
) -> list[dict[str, Any]]:
    priorities = (
        "chiffre d'affaires",
        "produit net bancaire",
        "primes",
        "résultat net",
        "total actif",
        "capitaux propres",
    )
    dossiers = []
    keys = sorted({(row["company_id"], int(row["exercise_year"])) for row in candidates})
    for company_id, exercise_year in keys:
        eligible = [
            row
            for row in candidates
            if row["company_id"] == company_id
            and int(row["exercise_year"]) == exercise_year
            and row["status"] == "REVIEW_REQUIRED"
        ]
        eligible.sort(
            key=lambda row: (
                next(
                    (
                        index
                        for index, phrase in enumerate(priorities)
                        if phrase in row["label"].casefold()
                    ),
                    len(priorities),
                ),
                row["page"][0] if row["page"] else 10**9,
                row["table_id"],
                row["row_index"],
            )
        )
        if not eligible:
            continue
        item = eligible[0]
        dossiers.append(
            {
                "company_id": company_id,
                "exercise_year": exercise_year,
                "status": "REVIEW_REQUIRED",
                "source_pdf": item["source_pdf"],
                "page": item["page"],
                "table_id": item["table_id"],
                "label": item["label"],
                "column": item["column"],
                "raw_value": item["raw_value"],
                "unit": item["unit"],
                "period": item["period"],
                "accounting_perimeter": item["reporting_scope"],
                "bbox": item["provenance"].get("bbox"),
                "reasons": item["reasons"],
                "decision": "Aucune validation humaine simulée; contrôle PDF requis.",
            }
        )
    if len(keys) != document_count:
        raise ValueError("Univers documentaire incomplet dans les annotations")
    return dossiers


def error_rows(technical: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in technical["results"]:
        common = {
            "company_id": item["company_id"],
            "exercise_year": item["exercise_year"],
        }
        metrics = item["metrics"]
        if item["pipeline_status"] != "PARSED":
            rows.append(
                {
                    **common,
                    "stage": "pipeline",
                    "severity": "MAJOR",
                    "code": "DOCUMENT_PARTIAL",
                    "count": 1,
                    "details": "Le document reste techniquement PARTIAL.",
                }
            )
        if metrics["docling_invalid_tables"]:
            rows.append(
                {
                    **common,
                    "stage": "docling",
                    "severity": "MAJOR",
                    "code": "INVALID_TABLES",
                    "count": metrics["docling_invalid_tables"],
                    "details": "Tables Docling vides ou structurellement invalides.",
                }
            )
        if item["stages"]["ocr"]["status"] == "FAILED":
            rows.append(
                {
                    **common,
                    "stage": "ocr",
                    "severity": "MAJOR",
                    "code": "NO_USABLE_OCR_TABLE",
                    "count": metrics["ocr_pages_processed"],
                    "details": "Pages OCR traitées sans table récupérable; Docling conservé.",
                }
            )
        if metrics["ambiguous_pages"]:
            rows.append(
                {
                    **common,
                    "stage": "quality",
                    "severity": "MINOR",
                    "code": "AMBIGUOUS_PAGES",
                    "count": len(metrics["ambiguous_pages"]),
                    "details": ",".join(map(str, metrics["ambiguous_pages"])),
                }
            )
    return rows


def run_report(
    references: Path = DEFAULT_REFERENCES,
    technical_path: Path = DEFAULT_TECHNICAL,
    output_root: Path = QUALITY_ROOT,
    registry_path: Path = DEFAULT_REGISTRY,
    prior_references_path: Path = DEFAULT_PRIOR_REFERENCES,
    prior_verified_path: Path = DEFAULT_PRIOR_VERIFIED,
    prior_coverage_path: Path = DEFAULT_PRIOR_COVERAGE,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    technical = read_json(Path(technical_path))
    reference_report = run_reference_validation(
        Path(references), output_root / "reference_validation"
    )
    candidates = candidate_records(reference_report)
    verified = add_version_metadata(
        strict_verified_export(reference_report), read_json(Path(references))
    )
    reviews = build_review_dossiers(candidates, len(reference_report["documents"]))
    errors = error_rows(technical)

    registry = read_json(Path(registry_path))
    validate_indicator_registry(registry)
    extension_coverage = build_coverage_matrix(reference_report, registry)
    combined_coverage = merge_coverage(
        _read_csv(Path(prior_coverage_path)), extension_coverage
    )
    prior_references = read_json(Path(prior_references_path))
    prior_documents = {
        (item["company_id"], int(item["exercise_year"]))
        for item in prior_references["documents"]
    }
    new_documents = {
        (item["company_id"], int(item["exercise_year"]))
        for item in reference_report["documents"]
    }
    if prior_documents & new_documents:
        raise ValueError("Un rapport antérieur a été retraité dans DATA-04.13")

    outputs = {
        "candidates": output_root / "candidate_observations.jsonl",
        "verified_csv": output_root / "verified_financial_observations.csv",
        "verified_jsonl": output_root / "verified_financial_observations.jsonl",
        "reviews": output_root / "review_dossiers/review_dossiers.json",
        "errors": output_root / "error_summary.csv",
        "coverage_extension": output_root / "financial_coverage_extension.csv",
        "coverage_after": output_root / "financial_coverage_after.csv",
    }
    _atomic_jsonl(outputs["candidates"], candidates)
    _atomic_csv(outputs["verified_csv"], verified)
    _atomic_jsonl(outputs["verified_jsonl"], verified)
    _atomic_json(
        outputs["reviews"],
        {"schema_version": SCHEMA_VERSION, "dossiers": reviews},
    )
    _atomic_csv(outputs["errors"], errors)
    _atomic_csv(outputs["coverage_extension"], extension_coverage)
    _atomic_csv(outputs["coverage_after"], combined_coverage)

    candidate_statuses = Counter(item["status"] for item in candidates)
    current_protected = protected_state()
    warehouse_hash = file_hash(WAREHOUSE_FILE) if WAREHOUSE_FILE.is_file() else None
    integrity = {
        "manifest_unchanged": file_hash(MANIFEST_FILE)
        == technical["manifest_sha256_after"],
        "source_pdfs_unchanged": all(
            file_hash(Path(item["source_pdf"])) == item["source_pdf_sha256"]
            for item in reference_report["documents"]
        ),
        "warehouse_unchanged": current_protected["warehouse"]
        == technical["protected_state"]["warehouse"],
        "streamlit_unchanged": current_protected["streamlit"]
        == technical["protected_state"]["streamlit"],
        "warehouse_sha256": warehouse_hash,
    }
    prior_verified_count = len(_read_csv(Path(prior_verified_path)))
    cache_replay = bool(technical.get("resume")) and all(
        item.get("pilot_cache_used") is True for item in technical["results"]
    )
    failures = [item for item in technical["results"] if item["pipeline_status"] == "FAILED"]
    recommendation = (
        "NO-GO"
        if failures or not all(value for key, value in integrity.items() if key != "warehouse_sha256")
        else "GO AVEC RÉSERVES"
        if any(item["pipeline_status"] == "PARTIAL" for item in technical["results"])
        else "GO"
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.13",
        "recommendation": recommendation,
        "selection": {
            "new_documents": sorted([list(item) for item in new_documents]),
            "prior_documents": len(prior_documents),
            "overlap_count": len(prior_documents & new_documents),
        },
        "processing": {
            "sequential": True,
            "max_parallel_documents": 1,
            "documents_processed": len(technical["results"]),
            "pages": technical["totals"]["pages"],
            "elapsed_seconds": technical["totals"]["elapsed_seconds"],
            "peak_rss_bytes": technical["totals"]["peak_rss_bytes"],
            "docling_tables": technical["totals"]["docling_tables"],
            "ocr_tables": technical["totals"]["ocr_tables"],
            "documents": technical["results"],
        },
        "quality_gate": {
            "candidate_values": len(candidates),
            "candidate_statuses": {
                status: candidate_statuses[status] for status in QUALITY_STATUSES
            },
            "reliable_references": reference_report["summary"]["reliable_references"],
            "new_verified": len(verified),
            "verified_period_roles": dict(
                Counter(row["period_role"] for row in verified)
            ),
            "manual_review_dossiers": len(reviews),
            "downstream_allowed_statuses": ["VERIFIED"],
            "non_verified_leak_count": sum(
                row["verification_status"] != "VERIFIED" for row in verified
            ),
        },
        "corpus_coverage": {
            "before": {
                "documents": len(prior_documents),
                "companies": len({item[0] for item in prior_documents}),
                "verified_observations": prior_verified_count,
            },
            "after": {
                "documents": len(prior_documents | new_documents),
                "companies": len({item[0] for item in prior_documents | new_documents}),
                "verified_observations": prior_verified_count + len(verified),
            },
        },
        "cache": {
            "resume_requested": technical.get("resume", False),
            "all_five_pipeline_results_reused": cache_replay,
            "artifact_hashes_validated_by_resume": cache_replay,
        },
        "integrity": integrity,
        "open_issues": [
            "Les cinq statuts documentaires restent PARTIAL.",
            "Quatre OCR ciblés n'ont produit aucune table exploitable; les artefacts Docling n'ont pas été écrasés.",
            "Les valeurs non comparées directement au PDF restent exclues de l'export VERIFIED.",
        ],
        "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
        "duckdb_ingestion_performed": False,
        "remaining_corpus_processed": False,
    }
    _atomic_json(output_root / "data_04_13_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--technical", type=Path, default=DEFAULT_TECHNICAL)
    parser.add_argument("--output-root", type=Path, default=QUALITY_ROOT)
    args = parser.parse_args()
    print(
        json.dumps(
            run_report(args.references, args.technical, args.output_root),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
