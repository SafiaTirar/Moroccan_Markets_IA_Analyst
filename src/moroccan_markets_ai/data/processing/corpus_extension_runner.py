"""Orchestration séquentielle et bornée de DATA-04.13."""

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
    protected_state,
    read_csv,
    selection_payload,
    validate_selection,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import atomic_json

PROCESSED_REFERENCES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json"
)
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/ammc/data_04_13"
QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_13"
SELECTION_FILE = QUALITY_ROOT / "data_04_13_selection.json"
TECHNICAL_REPORT = QUALITY_ROOT / "data_04_13_technical_report.json"
MAX_DOCUMENTS = 5
SCHEMA_VERSION = "1.0"
DATA_04_13_KEYS = [
    ("auto_hall", "2024"),
    ("s2m", "2023"),
    ("sothema", "2025"),
    ("wafa_assurance", "2024"),
    ("attijariwafa_bank", "2024"),
]
DATA_04_13_REASONS = [
    "Complète la séquence Auto Hall 2024-2025 avec un PDF TEXT modéré.",
    "Complète S2M 2023-2025 et apporte un cas MIXED contrôlé.",
    "Ajoute une entreprise pharmaceutique nouvelle avec un PDF TEXT et une seule page OCR candidate.",
    "Complète Wafa Assurance 2023-2025 avec un rapport TEXT.",
    "Ajoute 2024 à Attijariwafa Bank pour comparer 2024-2025 dans un secteur bancaire distinct.",
]


def processed_documents(reference_path: Path = PROCESSED_REFERENCES) -> list[dict[str, Any]]:
    return processed_documents_from_references((reference_path,), expected_count=8)


def processed_documents_from_references(
    reference_paths: tuple[Path, ...], *, expected_count: int
) -> list[dict[str, Any]]:
    documents = []
    for reference_path in reference_paths:
        payload = json.loads(Path(reference_path).read_text(encoding="utf-8"))
        for item in payload.get("documents", []):
            source_pdf = (PROJECT_ROOT / item["source_pdf"]).resolve()
            if (
                not source_pdf.is_file()
                or file_hash(source_pdf) != item["source_pdf_sha256"]
            ):
                raise ValueError(
                    f"Rapport traité absent ou modifié : {item['company_id']} "
                    f"{item['exercise_year']}"
                )
            documents.append(
                {
                    "company_id": item["company_id"],
                    "exercise_year": int(item["exercise_year"]),
                    "source_pdf": str(source_pdf),
                    "source_pdf_sha256": item["source_pdf_sha256"],
                }
            )
    keys = {(item["company_id"], item["exercise_year"]) for item in documents}
    if len(keys) != len(documents):
        raise ValueError("Un rapport traité est dupliqué entre les références")
    if len(documents) != expected_count:
        raise ValueError(
            f"{expected_count} rapports traités attendus, {len(documents)} trouvés"
        )
    return documents


def select_extension(
    audit_rows: list[dict[str, str]],
    plan_rows: list[dict[str, str]],
    processed: list[dict[str, Any]],
    *,
    candidate_keys: list[tuple[str, str]] | None = None,
    reasons: list[str] | None = None,
    allowed_classifications: frozenset[str] = frozenset({"TEXT", "MIXED"}),
) -> list[dict[str, Any]]:
    processed_keys = {
        (item["company_id"], str(item["exercise_year"])) for item in processed
    }
    audits = {(row["company_id"], row["exercise_year"]): row for row in audit_rows}
    plans = {(row["company_id"], row["exercise_year"]): row for row in plan_rows}
    candidate_keys = candidate_keys or DATA_04_13_KEYS
    reasons = reasons or DATA_04_13_REASONS
    if len(candidate_keys) != len(reasons):
        raise ValueError("Chaque rapport sélectionné doit avoir une justification")
    selected = []
    for order, (key, reason) in enumerate(
        zip(candidate_keys, reasons, strict=True), start=1
    ):
        if key in processed_keys:
            raise ValueError(f"Rapport déjà traité sélectionné : {key}")
        audit = audits.get(key)
        plan = plans.get(key)
        if not audit or not plan:
            raise ValueError(f"Rapport absent de l'audit ou du plan : {key}")
        if (
            audit.get("audit_status") != "SUCCESS"
            or audit.get("manifest_valid", "").lower() != "true"
            or audit.get("document_classification") not in allowed_classifications
        ):
            raise ValueError(f"Rapport non admissible pour le lot : {key}")
        selected.append(
            {
                **audit,
                **plan,
                "selection_order": order,
                "selection_reason": reason,
                "selection_score": int(plan["estimated_workload_units"]),
            }
        )
    return selected


def protected_extended_state() -> dict[str, Any]:
    state = protected_state()
    protected_roots = [
        PROJECT_ROOT / "src/moroccan_markets_ai/data/warehouse",
        PROJECT_ROOT / "src/moroccan_markets_ai/data/collectors/hcp_collector.py",
        PROJECT_ROOT / "src/moroccan_markets_ai/data/collectors/worldbank_collector.py",
        PROJECT_ROOT / "data/processed/macroeconomic",
    ]
    files = []
    for root in protected_roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            )
    state["mission_protected_files"] = {
        str(path): file_hash(path) for path in sorted(files)
    }
    return state


def run_extension(
    *,
    dry_run: bool = False,
    resume: bool = True,
    output_root: Path = OUTPUT_ROOT,
    quality_root: Path = QUALITY_ROOT,
) -> dict[str, Any]:
    processed = processed_documents()
    selections = select_extension(
        read_csv(AUDIT_FILE), read_csv(PLAN_FILE), processed
    )
    validations = validate_selection(
        selections, read_csv(MANIFEST_FILE), max_documents=MAX_DOCUMENTS
    )
    quality_root.mkdir(parents=True, exist_ok=True)
    selection = selection_payload(
        selections, validations, max_documents=MAX_DOCUMENTS
    )
    selection["mission"] = "DATA-04.13"
    selection["previously_processed"] = processed
    selection["processed_overlap_count"] = 0
    atomic_json(quality_root / SELECTION_FILE.name, selection)
    before = protected_extended_state()
    results: list[dict[str, Any]] = []
    stop_reason = None
    if not dry_run:
        results, stop_reason = execute_batch(
            selections, resume=resume, output_root=output_root
        )
    after = protected_extended_state()
    if before != after:
        raise RuntimeError("Un composant protégé a été modifié pendant DATA-04.13")
    report = {
        "schema_version": SCHEMA_VERSION,
        "mission": "DATA-04.13",
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
            "elapsed_seconds": round(sum(item.get("elapsed_seconds", 0) for item in results), 3),
            "peak_rss_bytes": max((item.get("peak_rss_bytes", 0) for item in results), default=0),
            "docling_tables": sum(item.get("metrics", {}).get("docling_tables_detected", 0) for item in results),
            "ocr_tables": sum(item.get("metrics", {}).get("ocr_tables_detected", 0) for item in results),
            "candidate_values": sum(item.get("metrics", {}).get("quality_values_total", 0) for item in results),
        },
        "protected_state": after,
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
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--quality-root", type=Path, default=QUALITY_ROOT)
    args = parser.parse_args()
    report = run_extension(
        dry_run=args.dry_run,
        resume=args.resume,
        output_root=args.output_root,
        quality_root=args.quality_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
