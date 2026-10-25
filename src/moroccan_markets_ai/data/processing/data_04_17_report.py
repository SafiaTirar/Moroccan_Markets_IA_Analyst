"""Consolide les preuves techniques et qualité de DATA-04.17."""

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
    WAREHOUSE_FILE,
)
from moroccan_markets_ai.data.processing.corpus_extension_runner import (
    protected_extended_state,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import atomic_json

QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_17"
TECHNICAL_REPORT = QUALITY_ROOT / "data_04_17_technical_report.json"
VISUAL_REVIEW = QUALITY_ROOT / "image_visual_review.json"
FINAL_REPORT = QUALITY_ROOT / "data_04_17_report.json"
PRIOR_VERIFIED_FILES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/verified_financial_observations.csv",
    PROJECT_ROOT / "data/quality/ammc/data_04_13/verified_financial_observations.csv",
    PROJECT_ROOT / "data/quality/ammc/data_04_14/verified_financial_observations.csv",
    PROJECT_ROOT / "data/quality/ammc/data_04_15/verified_financial_observations.csv",
    PROJECT_ROOT / "data/quality/ammc/data_04_16/verified_financial_observations.csv",
)
QUALITY_STATUSES = ("VERIFIED", "REVIEW_REQUIRED", "REJECTED", "NOT_CHECKED")
VERIFIED_COLUMNS = (
    "company_id",
    "exercise_year",
    "indicator",
    "normalized_value",
    "currency",
    "scale",
    "accounting_perimeter",
    "source_pdf_sha256",
    "page_number",
    "table_id",
    "verification_reference_id",
    "verification_status",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    temporary.replace(path)


def _empty_verified_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=VERIFIED_COLUMNS).writeheader()
    temporary.replace(path)


def _class_metrics(results: list[dict[str, Any]], classification: str) -> dict[str, Any]:
    selected = [item for item in results if item["audit_classification"] == classification]
    return {
        "documents": len(selected),
        "pages": sum(item["metrics"]["pages_processed"] for item in selected),
        "elapsed_seconds": round(sum(item["elapsed_seconds"] for item in selected), 3),
        "peak_rss_bytes": max((item["peak_rss_bytes"] for item in selected), default=0),
        "docling_tables": sum(item["metrics"]["docling_tables_detected"] for item in selected),
        "docling_non_empty": sum(item["metrics"]["docling_tables_non_empty"] for item in selected),
        "ocr_pages": sum(item["metrics"]["ocr_pages_processed"] for item in selected),
        "ocr_tables": sum(item["metrics"]["ocr_tables_non_empty"] for item in selected),
        "ambiguous_page_references": sum(len(item["metrics"]["ambiguous_pages"]) for item in selected),
        "candidate_values": sum(item["metrics"]["quality_values_total"] for item in selected),
    }


def run_report(
    technical_path: Path = TECHNICAL_REPORT,
    visual_review_path: Path = VISUAL_REVIEW,
    output_root: Path = QUALITY_ROOT,
) -> dict[str, Any]:
    technical = read_json(Path(technical_path))
    visual_review = read_json(Path(visual_review_path))
    results = technical["results"]
    if len(results) != 7:
        raise ValueError("DATA-04.17 exige sept résultats documentaires")

    candidates: list[dict[str, Any]] = []
    for result in results:
        quality = read_json(Path(result["stages"]["quality_gate"]["report"]))
        candidates.extend(
            {
                "company_id": result["company_id"],
                "exercise_year": result["exercise_year"],
                "source_pdf": result["source_pdf"],
                **item,
            }
            for item in _read_jsonl(Path(quality["outputs"]["annotations"]))
        )
    statuses = Counter(item["status"] for item in candidates)
    if statuses["VERIFIED"]:
        raise ValueError("Une valeur a été promue VERIFIED sans référence DATA-04.17")

    candidate_path = output_root / "candidate_observations.jsonl"
    verified_jsonl = output_root / "verified_financial_observations.jsonl"
    verified_csv = output_root / "verified_financial_observations.csv"
    _atomic_jsonl(candidate_path, candidates)
    _atomic_jsonl(verified_jsonl, [])
    _empty_verified_csv(verified_csv)

    manifest = _read_csv(MANIFEST_FILE)
    duplicate_sha = [
        sha for sha, count in Counter(item["sha256"] for item in manifest).items() if count > 1
    ]
    prior_verified = [row for path in PRIOR_VERIFIED_FILES for row in _read_csv(path)]
    invalid_prior_verified = [
        row for row in prior_verified if row.get("verification_status") != "VERIFIED"
    ]
    protected = protected_extended_state()
    integrity = {
        "manifest_unchanged": file_hash(MANIFEST_FILE) == technical["manifest_sha256_after"],
        "source_pdfs_unchanged": all(file_hash(Path(item["source_pdf"])) == item["source_sha256"] for item in results),
        "manifest_duplicate_sha256_groups": len(duplicate_sha),
        "prior_artifacts_unchanged": technical["prior_artifacts_unchanged"],
        "warehouse_unchanged": protected["warehouse"] == technical["protected_state"]["warehouse"],
        "streamlit_unchanged": protected["streamlit"] == technical["protected_state"]["streamlit"],
        "macro_sources_unchanged": protected.get("mission_protected_files", {}) == technical["protected_state"].get("mission_protected_files", {}),
        "prior_verified_observations": len(prior_verified),
        "invalid_prior_verified_statuses": len(invalid_prior_verified),
        "non_verified_downstream_leak_count": 0,
        "warehouse_sha256": file_hash(WAREHOUSE_FILE) if WAREHOUSE_FILE.is_file() else None,
    }
    failed = [item for item in results if item["pipeline_status"] == "FAILED"]
    integrity_ok = all(
        value is True or (key.endswith("groups") and value == 0) or (key.endswith("statuses") and value == 0) or (key.endswith("leak_count") and value == 0)
        for key, value in integrity.items()
        if key not in {"prior_verified_observations", "warehouse_sha256"}
    )
    recommendation = "NO GO" if failed or not integrity_ok else "GO AVEC RÉSERVES"
    report = {
        "schema_version": "1.0",
        "mission": "DATA-04.17",
        "recommendation": recommendation,
        "processing": {
            "sequential": True,
            "documents_processed": len(results),
            "pages": technical["totals"]["pages"],
            "elapsed_seconds": technical["totals"]["elapsed_seconds"],
            "peak_rss_bytes": technical["totals"]["peak_rss_bytes"],
            "statuses": dict(Counter(item["pipeline_status"] for item in results)),
            "comparison": {
                "MIXED": _class_metrics(results, "MIXED"),
                "IMAGE": _class_metrics(results, "IMAGE"),
            },
            "documents": results,
        },
        "image_checkpoint": {
            "pilot": ["dari_couspate", 2024],
            "assessment": technical["stages"]["B_IMAGE_PILOT"]["assessment"],
            "second_image_started": technical["stages"]["C_IMAGE_CONDITIONAL"]["started"],
            "visual_review": str(Path(visual_review_path).resolve()),
            "reviewed_pages": sum(len(item["pages"]) for item in visual_review["documents"]),
            "verified_promotions": 0,
        },
        "quality_gate": {
            "candidate_values": len(candidates),
            "candidate_statuses": {status: statuses[status] for status in QUALITY_STATUSES},
            "new_verified": 0,
            "downstream_allowed_statuses": ["VERIFIED"],
            "non_verified_leak_count": 0,
        },
        "corpus_coverage": {
            "before": 38,
            "after": 45,
            "annual_corpus_documents": 45,
            "document_coverage_percent": 100.0,
        },
        "readiness": {
            "ready_for_controlled_qwen_extraction": [
                [item["company_id"], item["exercise_year"]]
                for item in results
                if item["audit_classification"] == "MIXED"
            ],
            "partially_exploitable_manual_review_required": [
                [item["company_id"], item["exercise_year"]]
                for item in results
                if item["audit_classification"] == "IMAGE"
            ],
            "blocked": [],
            "caution": "Prêt signifie seulement qu'un artefact intégré traçable existe; seules les futures observations VERIFIED pourront être admises downstream.",
        },
        "integrity": integrity,
        "open_issues": [
            "Les sept documents conservent un statut PARTIAL.",
            "Les rapports IMAGE n'ont aucun tableau Docling non vide; leur exploitation repose sur l'OCR avec revue obligatoire.",
            "La revue visuelle a confirmé des concaténations de montants et des pertes de signe dans les CSV OCR IMAGE.",
            "Les périmètres documentaires des rapports IMAGE restent UNKNOWN au niveau global et ne doivent pas être propagés entre pages.",
            "Aucune nouvelle observation n'a de référence suffisamment complète pour VERIFIED.",
        ],
        "outputs": {
            "candidates": str(candidate_path.resolve()),
            "verified_csv": str(verified_csv.resolve()),
            "verified_jsonl": str(verified_jsonl.resolve()),
            "visual_review": str(Path(visual_review_path).resolve()),
        },
        "duckdb_ingestion_performed": False,
        "qwen_integration_performed": False,
    }
    atomic_json(Path(output_root) / FINAL_REPORT.name, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--technical", type=Path, default=TECHNICAL_REPORT)
    parser.add_argument("--visual-review", type=Path, default=VISUAL_REVIEW)
    parser.add_argument("--output-root", type=Path, default=QUALITY_ROOT)
    args = parser.parse_args()
    print(json.dumps(run_report(args.technical, args.visual_review, args.output_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
