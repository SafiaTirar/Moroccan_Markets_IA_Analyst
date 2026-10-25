"""Orchestration séquentielle avec point de contrôle IMAGE de DATA-04.17."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.document_integrator import file_hash
from moroccan_markets_ai.data.processing.corpus_batch_runner import (
    AUDIT_FILE,
    MANIFEST_FILE,
    PLAN_FILE,
    execute_batch,
    read_csv,
    selection_payload,
    validate_selection,
)
from moroccan_markets_ai.data.processing.corpus_extension_runner import (
    processed_documents_from_references,
    protected_extended_state,
    select_extension,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import (
    MAX_SAFE_RSS_BYTES,
    atomic_json,
)

QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_17"
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/ammc/data_04_17"
SELECTION_FILE = QUALITY_ROOT / "data_04_17_selection.json"
TECHNICAL_REPORT = QUALITY_ROOT / "data_04_17_technical_report.json"
MAX_DOCUMENTS = 7
REFERENCE_FILES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json",
    PROJECT_ROOT / "data/quality/ammc/data_04_13/references/data_04_13_references.json",
    PROJECT_ROOT / "data/quality/ammc/data_04_14/references/data_04_14_references.json",
    PROJECT_ROOT / "data/quality/ammc/data_04_15/references/data_04_15_references.json",
)
PRIOR_TECHNICAL_REPORT = (
    PROJECT_ROOT / "data/quality/ammc/data_04_16/data_04_16_technical_report.json"
)
PRIOR_MISSION_ROOTS = (
    PROJECT_ROOT / "data/quality/ammc/data_04_16",
    PROJECT_ROOT / "data/processed/ammc/data_04_16",
)
MIXED_KEYS = [
    ("totalenergies_marketing_maroc", "2024"),
    ("aradei_capital", "2024"),
    ("totalenergies_marketing_maroc", "2023"),
    ("totalenergies_marketing_maroc", "2025"),
    ("aradei_capital", "2023"),
]
IMAGE_KEYS = [("dari_couspate", "2024"), ("dari_couspate", "2025")]
SELECTION_KEYS = MIXED_KEYS + IMAGE_KEYS
SELECTION_REASONS = [
    "Étape A MIXED; charge la plus faible du lot restant.",
    "Étape A MIXED; deuxième charge la plus faible.",
    "Étape A MIXED; continuité historique TotalEnergies.",
    "Étape A MIXED; complète TotalEnergies 2023-2025.",
    "Étape A MIXED; complète Aradei 2023-2024.",
    "Étape B IMAGE; pilote OCR-first obligatoire.",
    "Étape C IMAGE; autorisé uniquement après validation du pilote 2024.",
]
SCHEMA_VERSION = "1.0"


def prior_artifact_state() -> dict[str, str]:
    files = [
        path
        for root in PRIOR_MISSION_ROOTS
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    return {str(path): file_hash(path) for path in sorted(files)}


def previously_processed_documents() -> list[dict[str, Any]]:
    documents = processed_documents_from_references(REFERENCE_FILES, expected_count=28)
    technical = json.loads(PRIOR_TECHNICAL_REPORT.read_text(encoding="utf-8"))
    for item in technical.get("results", []):
        source = Path(item["source_pdf"]).resolve()
        if not source.is_file() or file_hash(source) != item["source_sha256"]:
            raise ValueError(
                f"Rapport DATA-04.16 absent ou modifié : {item['company_id']} "
                f"{item['exercise_year']}"
            )
        documents.append(
            {
                "company_id": item["company_id"],
                "exercise_year": int(item["exercise_year"]),
                "source_pdf": str(source),
                "source_pdf_sha256": item["source_sha256"],
            }
        )
    keys = {(item["company_id"], item["exercise_year"]) for item in documents}
    if len(documents) != 38 or len(keys) != 38:
        raise ValueError(
            "La reprise DATA-04.17 exige exactement 38 rapports antérieurs uniques"
        )
    return documents


def stage_is_safe(results: list[dict[str, Any]], expected_count: int) -> bool:
    return len(results) == expected_count and all(
        item.get("pipeline_status") != "FAILED"
        and not item.get("batch_control", {}).get("document_stopped", False)
        for item in results
    )


def image_pilot_assessment(result: dict[str, Any] | None) -> dict[str, Any]:
    checks = {
        "result_present": result is not None,
        "pipeline_not_failed": bool(result)
        and result.get("pipeline_status") != "FAILED",
        "ocr_first": bool(result)
        and result.get("extraction_strategy") == "OCR_FIRST",
        "memory_below_guard": bool(result)
        and int(result.get("peak_rss_bytes", MAX_SAFE_RSS_BYTES))
        < MAX_SAFE_RSS_BYTES,
        "all_pages_processed": bool(result)
        and int(result.get("metrics", {}).get("pages_failed", 1)) == 0,
        "ocr_completed_without_page_failure": bool(result)
        and int(result.get("metrics", {}).get("ocr_pages_failed", 1)) == 0,
        "ocr_table_recovered": bool(result)
        and int(result.get("metrics", {}).get("ocr_tables_non_empty", 0)) > 0,
        "active_table_available": bool(result)
        and int(result.get("metrics", {}).get("integrated_active_tables", 0)) > 0,
        "table_page_provenance_complete": bool(result)
        and int(
            result.get("metrics", {}).get("tables_missing_page_reference", 1)
        )
        == 0,
        "artifacts_hashed": bool(result) and bool(result.get("artifacts_sha256")),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
    }


def _totals(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "documents": len(results),
        "pages": sum(item.get("metrics", {}).get("pages_processed", 0) for item in results),
        "elapsed_seconds": round(sum(item.get("elapsed_seconds", 0) for item in results), 3),
        "peak_rss_bytes": max((item.get("peak_rss_bytes", 0) for item in results), default=0),
        "docling_tables": sum(item.get("metrics", {}).get("docling_tables_detected", 0) for item in results),
        "ocr_tables": sum(item.get("metrics", {}).get("ocr_tables_detected", 0) for item in results),
        "candidate_values": sum(item.get("metrics", {}).get("quality_values_total", 0) for item in results),
    }


def run_extension(
    *,
    dry_run: bool = False,
    resume: bool = True,
    output_root: Path = OUTPUT_ROOT,
    quality_root: Path = QUALITY_ROOT,
) -> dict[str, Any]:
    processed = previously_processed_documents()
    selections = select_extension(
        read_csv(AUDIT_FILE),
        read_csv(PLAN_FILE),
        processed,
        candidate_keys=SELECTION_KEYS,
        reasons=SELECTION_REASONS,
        allowed_classifications=frozenset({"MIXED", "IMAGE"}),
    )
    validations = validate_selection(
        selections, read_csv(MANIFEST_FILE), max_documents=MAX_DOCUMENTS
    )
    if [item["document_classification"] for item in selections[:5]] != ["MIXED"] * 5:
        raise ValueError("L'étape A doit contenir cinq rapports MIXED")
    if [item["document_classification"] for item in selections[5:]] != ["IMAGE"] * 2:
        raise ValueError("Les étapes B/C doivent contenir deux rapports IMAGE")
    if [item["recommended_strategy"] for item in selections[5:]] != ["OCR"] * 2:
        raise ValueError("Les rapports IMAGE doivent être planifiés en OCR-first")

    quality_root.mkdir(parents=True, exist_ok=True)
    selection = selection_payload(selections, validations, max_documents=MAX_DOCUMENTS)
    selection.update(
        {
            "mission": "DATA-04.17",
            "previously_processed": processed,
            "processed_overlap_count": 0,
            "stages": {
                "A_MIXED": [list(item) for item in MIXED_KEYS],
                "B_IMAGE_PILOT": list(IMAGE_KEYS[0]),
                "C_IMAGE_CONDITIONAL": list(IMAGE_KEYS[1]),
            },
        }
    )
    atomic_json(quality_root / SELECTION_FILE.name, selection)

    protected_before = protected_extended_state()
    prior_before = prior_artifact_state()
    mixed_results: list[dict[str, Any]] = []
    pilot_results: list[dict[str, Any]] = []
    second_image_results: list[dict[str, Any]] = []
    stop_reason = None
    mixed_gate_passed = dry_run
    assessment = {"passed": dry_run, "checks": {}, "failed_checks": []}
    if not dry_run:
        mixed_results, _ = execute_batch(
            selections[:5],
            resume=resume,
            output_root=output_root,
            continue_after_document_stop=True,
        )
        mixed_gate_passed = stage_is_safe(mixed_results, 5)
        if mixed_gate_passed:
            pilot_results, _ = execute_batch(
                selections[5:6], resume=resume, output_root=output_root
            )
            assessment = image_pilot_assessment(
                pilot_results[0] if pilot_results else None
            )
            if assessment["passed"]:
                second_image_results, _ = execute_batch(
                    selections[6:7], resume=resume, output_root=output_root
                )
            else:
                stop_reason = "IMAGE_PILOT_GATE_FAILED"
        else:
            stop_reason = "MIXED_STAGE_NOT_VALIDATED"

    results = mixed_results + pilot_results + second_image_results
    protected_after = protected_extended_state()
    prior_after = prior_artifact_state()
    if protected_before != protected_after:
        raise RuntimeError("Un composant protégé a été modifié pendant DATA-04.17")
    if prior_before != prior_after:
        raise RuntimeError("Un artefact DATA-04.16 a été modifié pendant DATA-04.17")

    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.17",
        "status": "DRY_RUN" if dry_run else "STOPPED" if stop_reason else "COMPLETED",
        "dry_run": dry_run,
        "resume": resume,
        "authorized_documents": [[item["company_id"], int(item["exercise_year"])] for item in selections],
        "previously_processed_count": len(processed),
        "processed_overlap_count": 0,
        "processed_document_count": len(results),
        "remaining_authorized_count": len(selections) - len(results),
        "stop_reason": stop_reason,
        "stages": {
            "A_MIXED": {"gate_passed": mixed_gate_passed, "results": mixed_results, "totals": _totals(mixed_results)},
            "B_IMAGE_PILOT": {"started": bool(pilot_results), "assessment": assessment, "results": pilot_results, "totals": _totals(pilot_results)},
            "C_IMAGE_CONDITIONAL": {"started": bool(second_image_results), "results": second_image_results, "totals": _totals(second_image_results)},
        },
        "results": results,
        "totals": _totals(results),
        "protected_state": protected_after,
        "prior_artifact_count": len(prior_after),
        "prior_artifacts_unchanged": True,
        "manifest_sha256_after": file_hash(MANIFEST_FILE),
        "selection_file": str((quality_root / SELECTION_FILE.name).resolve()),
        "financial_validation_pending": not dry_run,
    }
    atomic_json(quality_root / TECHNICAL_REPORT.name, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    print(json.dumps(run_extension(dry_run=args.dry_run, resume=args.resume), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
