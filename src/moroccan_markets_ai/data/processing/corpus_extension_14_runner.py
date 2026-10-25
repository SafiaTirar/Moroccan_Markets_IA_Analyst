"""Orchestration séquentielle du lot DATA-04.14."""

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
    MAX_DOCUMENTS,
    processed_documents_from_references,
    protected_extended_state,
    select_extension,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import atomic_json

QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_14"
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/ammc/data_04_14"
SELECTION_FILE = QUALITY_ROOT / "data_04_14_selection.json"
TECHNICAL_REPORT = QUALITY_ROOT / "data_04_14_technical_report.json"
REFERENCE_FILES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json",
    PROJECT_ROOT
    / "data/quality/ammc/data_04_13/references/data_04_13_references.json",
)
PRIOR_MISSION_ROOTS = (
    PROJECT_ROOT / "data/quality/ammc/data_04_13",
    PROJECT_ROOT / "data/processed/ammc/data_04_13",
)
SELECTION_KEYS = [
    ("lesieur_cristal", "2023"),
    ("lesieur_cristal", "2025"),
    ("auto_hall", "2023"),
    ("sothema", "2024"),
    ("lesieur_cristal", "2024"),
]
SELECTION_REASONS = [
    "Ouvre la série Lesieur Cristal avec le rapport TEXT le moins chargé du corpus restant.",
    "Ajoute l'extrémité 2025 de la série Lesieur Cristal avec une seule page OCR candidate.",
    "Complète Auto Hall 2023-2025 avec un rapport MIXED de charge modérée.",
    "Ajoute 2024 à Sothema afin de permettre une comparaison 2024-2025.",
    "Complète la série Lesieur Cristal 2023-2025; traitement TEXT séquentiel.",
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


def run_extension(
    *,
    dry_run: bool = False,
    resume: bool = True,
    output_root: Path = OUTPUT_ROOT,
    quality_root: Path = QUALITY_ROOT,
) -> dict[str, Any]:
    processed = processed_documents_from_references(
        REFERENCE_FILES, expected_count=13
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
    quality_root.mkdir(parents=True, exist_ok=True)
    selection = selection_payload(
        selections, validations, max_documents=MAX_DOCUMENTS
    )
    selection.update(
        {
            "mission": "DATA-04.14",
            "previously_processed": processed,
            "processed_overlap_count": 0,
        }
    )
    atomic_json(quality_root / SELECTION_FILE.name, selection)

    protected_before = protected_extended_state()
    prior_before = prior_artifact_state()
    results: list[dict[str, Any]] = []
    stop_reason = None
    if not dry_run:
        results, stop_reason = execute_batch(
            selections, resume=resume, output_root=output_root
        )
    protected_after = protected_extended_state()
    prior_after = prior_artifact_state()
    if protected_before != protected_after:
        raise RuntimeError("Un composant protégé a été modifié pendant DATA-04.14")
    if prior_before != prior_after:
        raise RuntimeError("Un artefact DATA-04.13 a été modifié pendant DATA-04.14")

    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.14",
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
        "stop_reason": stop_reason,
        "results": results,
        "totals": {
            "pages": sum(item.get("metrics", {}).get("pages_processed", 0) for item in results),
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
        },
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
