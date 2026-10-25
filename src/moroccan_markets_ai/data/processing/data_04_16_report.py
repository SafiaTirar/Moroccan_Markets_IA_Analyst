"""Consolide les résultats techniques et financiers de DATA-04.16."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.document_integrator import file_hash, read_json
from moroccan_markets_ai.data.processing.corpus_batch_runner import (
    MANIFEST_FILE,
    WAREHOUSE_FILE,
)
from moroccan_markets_ai.data.processing.corpus_extension_runner import (
    protected_extended_state,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
from moroccan_markets_ai.data.processing.data_04_13_report import (
    QUALITY_STATUSES,
    _read_csv,
    add_version_metadata,
    build_review_dossiers,
    error_rows,
    merge_coverage,
    strict_verified_export,
)
from moroccan_markets_ai.data.processing.data_04_14_report import _prior_documents
from moroccan_markets_ai.data.processing.financial_multisector_report import (
    _atomic_csv,
    build_coverage_matrix,
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

QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_16"
DEFAULT_REFERENCES = QUALITY_ROOT / "references/data_04_16_references.json"
DEFAULT_TECHNICAL = QUALITY_ROOT / "data_04_16_technical_report.json"
PRIOR_REFERENCE_FILES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_13/references/data_04_13_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_14/references/data_04_14_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_15/references/data_04_15_references.json",
)
PRIOR_VERIFIED_FILES = (
    PROJECT_ROOT
    / "data/quality/ammc/data_06_2/verified_financial_observations.csv",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_13/verified_financial_observations.csv",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_14/verified_financial_observations.csv",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_15/verified_financial_observations.csv",
)
DEFAULT_PRIOR_COVERAGE = (
    PROJECT_ROOT / "data/quality/ammc/data_04_15/financial_coverage_after.csv"
)
SCHEMA_VERSION = "1.0"


def _document_key(item: dict[str, Any]) -> tuple[str, int]:
    return item["company_id"], int(item["exercise_year"])


def candidate_records_all(
    reference_report: dict[str, Any], technical: dict[str, Any]
) -> list[dict[str, Any]]:
    patched_reports = {
        _document_key(item): item["quality_report"]
        for item in reference_report["documents"]
    }
    records: list[dict[str, Any]] = []
    for item in technical["results"]:
        report_path = patched_reports.get(
            _document_key(item), item["stages"]["quality_gate"]["report"]
        )
        quality = read_json(Path(report_path))
        records.extend(
            {
                "company_id": item["company_id"],
                "exercise_year": int(item["exercise_year"]),
                "source_pdf": item["source_pdf"],
                **candidate,
            }
            for candidate in _load_jsonl(Path(quality["outputs"]["annotations"]))
        )
    return records


def _class_metrics(results: list[dict[str, Any]], classification: str) -> dict[str, Any]:
    selected = [
        item for item in results if item["audit_classification"] == classification
    ]
    return {
        "documents": len(selected),
        "pages": sum(item["metrics"]["pages_processed"] for item in selected),
        "elapsed_seconds": round(sum(item["elapsed_seconds"] for item in selected), 3),
        "peak_rss_bytes": max(
            (item["peak_rss_bytes"] for item in selected), default=0
        ),
        "docling_tables": sum(
            item["metrics"]["docling_tables_detected"] for item in selected
        ),
        "docling_non_empty": sum(
            item["metrics"]["docling_tables_non_empty"] for item in selected
        ),
        "ocr_pages": sum(
            item["metrics"]["ocr_pages_processed"] for item in selected
        ),
        "ocr_tables": sum(
            item["metrics"]["ocr_tables_non_empty"] for item in selected
        ),
        "ambiguous_pages": sum(
            len(item["metrics"]["ambiguous_pages"]) for item in selected
        ),
        "candidate_values": sum(
            item["metrics"]["quality_values_total"] for item in selected
        ),
    }


def run_report(
    references: Path = DEFAULT_REFERENCES,
    technical_path: Path = DEFAULT_TECHNICAL,
    output_root: Path = QUALITY_ROOT,
    registry_path: Path = DEFAULT_REGISTRY,
    prior_coverage_path: Path = DEFAULT_PRIOR_COVERAGE,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    technical = read_json(Path(technical_path))
    reference_report = run_reference_validation(
        Path(references), output_root / "reference_validation"
    )
    candidates = candidate_records_all(reference_report, technical)
    specification = read_json(Path(references))
    verified = add_version_metadata(
        strict_verified_export(reference_report), specification
    )
    reviews = build_review_dossiers(candidates, len(technical["results"]))
    errors = error_rows(technical)

    registry = read_json(Path(registry_path))
    validate_indicator_registry(registry)
    extension_coverage = build_coverage_matrix(reference_report, registry)
    combined_coverage = merge_coverage(
        _read_csv(Path(prior_coverage_path)), extension_coverage
    )
    prior_documents = _prior_documents(PRIOR_REFERENCE_FILES)
    processed_documents = {_document_key(item) for item in technical["results"]}
    referenced_documents = {
        _document_key(item) for item in reference_report["documents"]
    }
    overlap = prior_documents & processed_documents
    if overlap:
        raise ValueError(f"Rapport antérieur retraité dans DATA-04.16 : {sorted(overlap)}")
    if not referenced_documents <= processed_documents:
        raise ValueError("Le référentiel contient un rapport hors du lot DATA-04.16")
    if len(processed_documents) != 10:
        raise ValueError("DATA-04.16 doit contenir exactement dix nouveaux rapports")

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
    current_protected = protected_extended_state()
    warehouse_hash = file_hash(WAREHOUSE_FILE) if WAREHOUSE_FILE.is_file() else None
    integrity = {
        "manifest_unchanged": (
            file_hash(MANIFEST_FILE) == technical["manifest_sha256_after"]
        ),
        "source_pdfs_unchanged": all(
            file_hash(Path(item["source_pdf"])) == item["source_sha256"]
            for item in technical["results"]
        ),
        "warehouse_unchanged": (
            current_protected["warehouse"]
            == technical["protected_state"]["warehouse"]
        ),
        "streamlit_unchanged": (
            current_protected["streamlit"]
            == technical["protected_state"]["streamlit"]
        ),
        "macro_and_warehouse_sources_unchanged": (
            current_protected.get("mission_protected_files", {})
            == technical["protected_state"].get("mission_protected_files", {})
        ),
        "prior_mission_artifacts_unchanged": technical[
            "prior_artifacts_unchanged"
        ],
        "warehouse_sha256": warehouse_hash,
    }
    prior_verified_count = sum(
        len(_read_csv(path)) for path in PRIOR_VERIFIED_FILES
    )
    cache_replay = bool(technical.get("resume")) and all(
        item.get("pilot_cache_used") is True for item in technical["results"]
    )
    failures = [
        item for item in technical["results"] if item["pipeline_status"] == "FAILED"
    ]
    integrity_checks = [
        value for key, value in integrity.items() if key != "warehouse_sha256"
    ]
    recommendation = (
        "NO-GO"
        if failures or not all(integrity_checks)
        else "GO AVEC RÉSERVES"
        if any(
            item["pipeline_status"] == "PARTIAL" for item in technical["results"]
        )
        else "GO"
    )
    all_documents = prior_documents | processed_documents
    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.16",
        "recommendation": recommendation,
        "selection": {
            "new_documents": sorted([list(item) for item in processed_documents]),
            "prior_documents": len(prior_documents),
            "overlap_count": len(overlap),
            "financially_sampled_documents": len(referenced_documents),
        },
        "processing": {
            "sequential": True,
            "max_parallel_documents": 1,
            "text_gate_passed_before_mixed": technical["sublots"]["A_TEXT"][
                "validated_for_mixed_start"
            ],
            "documents_processed": len(technical["results"]),
            "pages": technical["totals"]["pages"],
            "elapsed_seconds": technical["totals"]["elapsed_seconds"],
            "peak_rss_bytes": technical["totals"]["peak_rss_bytes"],
            "docling_tables": technical["totals"]["docling_tables"],
            "ocr_tables": technical["totals"]["ocr_tables"],
            "comparison": {
                "TEXT": _class_metrics(technical["results"], "TEXT"),
                "MIXED": _class_metrics(technical["results"], "MIXED"),
            },
            "documents": technical["results"],
        },
        "quality_gate": {
            "candidate_values": len(candidates),
            "candidate_statuses": {
                status: candidate_statuses[status] for status in QUALITY_STATUSES
            },
            "sample_candidates_examined": reference_report["summary"][
                "sample_candidates_examined"
            ],
            "reliable_references": reference_report["summary"][
                "reliable_references"
            ],
            "sample_rejected": reference_report["summary"]["rejected"],
            "new_verified": len(verified),
            "manual_review_dossiers": len(reviews),
            "downstream_allowed_statuses": ["VERIFIED"],
            "non_verified_leak_count": sum(
                row["verification_status"] != "VERIFIED" for row in verified
            ),
        },
        "corpus_coverage": {
            "before": {
                "documents": len(prior_documents),
                "verified_observations": prior_verified_count,
            },
            "after": {
                "documents": len(all_documents),
                "verified_observations": prior_verified_count + len(verified),
            },
            "annual_corpus_documents": 45,
            "document_coverage_percent": round(100 * len(all_documents) / 45, 1),
        },
        "cache": {
            "resume_requested": technical.get("resume", False),
            "all_ten_pipeline_results_reused": cache_replay,
            "artifact_hashes_validated_by_resume": cache_replay,
        },
        "integrity": integrity,
        "open_issues": [
            "Les dix statuts documentaires restent PARTIAL.",
            "Les trois rapports Stroc restent financièrement non vérifiés: leurs tableaux sources sont scannés et les sorties OCR demeurent ambiguës.",
            "Une valeur Sonasid 2023 est REJECTED après confusion déterministe entre séparateur de milliers et séparateur décimal.",
            "Les valeurs non comparées directement au PDF restent exclues de l'export VERIFIED.",
        ],
        "remaining_plan": {
            "MIXED_first": [
                ["totalenergies_marketing_maroc", 2024],
                ["aradei_capital", 2024],
                ["totalenergies_marketing_maroc", 2023],
                ["totalenergies_marketing_maroc", 2025],
                ["aradei_capital", 2023],
            ],
            "IMAGE_last": [
                ["dari_couspate", 2024],
                ["dari_couspate", 2025],
            ],
            "image_strategy": "Pilote unitaire OCR-first avec plafond mémoire, reprise et revue visuelle avant le second IMAGE.",
        },
        "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
        "duckdb_ingestion_performed": False,
        "qwen_integration_performed": False,
    }
    _atomic_json(output_root / "data_04_16_report.json", report)
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
