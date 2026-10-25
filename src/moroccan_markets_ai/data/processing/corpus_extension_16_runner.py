"""Orchestration séquentielle en deux sous-lots de DATA-04.16."""

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
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import atomic_json

QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_16"
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/ammc/data_04_16"
SELECTION_FILE = QUALITY_ROOT / "data_04_16_selection.json"
TECHNICAL_REPORT = QUALITY_ROOT / "data_04_16_technical_report.json"
MAX_DOCUMENTS = 10
REFERENCE_FILES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_13/references/data_04_13_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_14/references/data_04_14_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_15/references/data_04_15_references.json",
)
PRIOR_MISSION_ROOTS = (
    PROJECT_ROOT / "data/quality/ammc/data_04_15",
    PROJECT_ROOT / "data/processed/ammc/data_04_15",
)
TEXT_KEYS = [
    ("sonasid", "2024"),
    ("managem", "2025"),
    ("sonasid", "2023"),
    ("cosumar", "2025"),
    ("marsa_maroc", "2025"),
    ("marsa_maroc", "2023"),
    ("sothema", "2023"),
]
MIXED_KEYS = [
    ("stroc", "2023"),
    ("stroc", "2024"),
    ("stroc", "2025"),
]
SELECTION_KEYS = TEXT_KEYS + MIXED_KEYS
SELECTION_REASONS = [
    "Sous-lot A TEXT; complète la série Sonasid avec une charge modérée.",
    "Sous-lot A TEXT; complète Managem 2023-2025.",
    "Sous-lot A TEXT; complète la série historique Sonasid.",
    "Sous-lot A TEXT; complète Cosumar 2023-2025.",
    "Sous-lot A TEXT; étend Marsa Maroc à l'exercice 2025.",
    "Sous-lot A TEXT; complète la série Marsa Maroc 2023-2025.",
    "Sous-lot A TEXT; nouveau rapport pharmaceutique de charge admissible.",
    "Sous-lot B MIXED; première année Stroc après validation du sous-lot A.",
    "Sous-lot B MIXED; deuxième année Stroc avec diagnostic OCR renforcé.",
    "Sous-lot B MIXED; complète Stroc 2023-2025.",
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


def sublot_is_safe(results: list[dict[str, Any]], expected_count: int) -> bool:
    return len(results) == expected_count and all(
        item.get("pipeline_status") != "FAILED"
        and not item.get("batch_control", {}).get("document_stopped", False)
        for item in results
    )


def _totals(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pages": sum(
            item.get("metrics", {}).get("pages_processed", 0) for item in results
        ),
        "elapsed_seconds": round(
            sum(item.get("elapsed_seconds", 0) for item in results), 3
        ),
        "peak_rss_bytes": max(
            (item.get("peak_rss_bytes", 0) for item in results), default=0
        ),
        "docling_tables": sum(
            item.get("metrics", {}).get("docling_tables_detected", 0)
            for item in results
        ),
        "ocr_tables": sum(
            item.get("metrics", {}).get("ocr_tables_detected", 0)
            for item in results
        ),
        "candidate_values": sum(
            item.get("metrics", {}).get("quality_values_total", 0)
            for item in results
        ),
    }


def run_extension(
    *,
    dry_run: bool = False,
    resume: bool = True,
    output_root: Path = OUTPUT_ROOT,
    quality_root: Path = QUALITY_ROOT,
) -> dict[str, Any]:
    processed = processed_documents_from_references(
        REFERENCE_FILES, expected_count=28
    )
    selections = select_extension(
        read_csv(AUDIT_FILE),
        read_csv(PLAN_FILE),
        processed,
        candidate_keys=SELECTION_KEYS,
        reasons=SELECTION_REASONS,
    )
    validations = validate_selection(
        selections, read_csv(MANIFEST_FILE), max_documents=MAX_DOCUMENTS
    )
    if [item["document_classification"] for item in selections[:7]] != [
        "TEXT"
    ] * 7:
        raise ValueError("Le sous-lot A doit contenir sept rapports TEXT")
    if [item["document_classification"] for item in selections[7:]] != [
        "MIXED"
    ] * 3:
        raise ValueError("Le sous-lot B doit contenir trois rapports MIXED")

    quality_root.mkdir(parents=True, exist_ok=True)
    selection = selection_payload(
        selections, validations, max_documents=MAX_DOCUMENTS
    )
    selection.update(
        {
            "mission": "DATA-04.16",
            "previously_processed": processed,
            "processed_overlap_count": 0,
            "sublots": {
                "A_TEXT": [list(item) for item in TEXT_KEYS],
                "B_MIXED": [list(item) for item in MIXED_KEYS],
            },
        }
    )
    atomic_json(quality_root / SELECTION_FILE.name, selection)

    protected_before = protected_extended_state()
    prior_before = prior_artifact_state()
    text_results: list[dict[str, Any]] = []
    mixed_results: list[dict[str, Any]] = []
    stop_reason = None
    text_gate_passed = dry_run
    if not dry_run:
        text_results, _ = execute_batch(
            selections[:7],
            resume=resume,
            output_root=output_root,
            continue_after_document_stop=True,
        )
        text_gate_passed = sublot_is_safe(text_results, len(TEXT_KEYS))
        if text_gate_passed:
            mixed_results, _ = execute_batch(
                selections[7:],
                resume=resume,
                output_root=output_root,
                continue_after_document_stop=True,
            )
        else:
            stop_reason = "TEXT_SUBLOT_NOT_VALIDATED"

    results = text_results + mixed_results
    protected_after = protected_extended_state()
    prior_after = prior_artifact_state()
    if protected_before != protected_after:
        raise RuntimeError("Un composant protégé a été modifié pendant DATA-04.16")
    if prior_before != prior_after:
        raise RuntimeError("Un artefact DATA-04.15 a été modifié pendant DATA-04.16")

    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.16",
        "status": "DRY_RUN" if dry_run else "STOPPED" if stop_reason else "COMPLETED",
        "dry_run": dry_run,
        "resume": resume,
        "authorized_documents": [
            [item["company_id"], int(item["exercise_year"])]
            for item in selections
        ],
        "previously_processed_count": len(processed),
        "processed_overlap_count": 0,
        "processed_document_count": len(results),
        "remaining_authorized_count": len(selections) - len(results),
        "stop_reason": stop_reason,
        "sublots": {
            "A_TEXT": {
                "validated_for_mixed_start": text_gate_passed,
                "results": text_results,
                "totals": _totals(text_results),
            },
            "B_MIXED": {
                "started": bool(mixed_results),
                "results": mixed_results,
                "totals": _totals(mixed_results),
            },
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
    print(
        json.dumps(
            run_extension(dry_run=args.dry_run, resume=args.resume),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
