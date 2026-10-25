"""Orchestration séquentielle et bornée de DATA-04.12."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf

from moroccan_markets_ai.data.parsers.document_integrator import file_hash
from moroccan_markets_ai.data.processing.corpus_pdf_audit import PROJECT_ROOT
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import (
    MAX_SAFE_RSS_BYTES,
    atomic_json,
    parse_page_list,
    reusable_result,
)

AUDIT_DIR = PROJECT_ROOT / "data/processed/ammc/corpus_audit"
AUDIT_FILE = AUDIT_DIR / "annual_pdf_audit.csv"
PLAN_FILE = AUDIT_DIR / "annual_processing_plan.csv"
MANIFEST_FILE = PROJECT_ROOT / "data/raw/ammc/corpus/download_manifest.csv"
COVERAGE_FILE = (
    PROJECT_ROOT
    / "data/quality/ammc/data_04_11/financial_coverage_matrix.csv"
)
REFERENCE_FILE = (
    PROJECT_ROOT / "config/quality/ammc/annual_pilot_references.json"
)
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/ammc/data_04_12"
QUALITY_ROOT = PROJECT_ROOT / "data/quality/ammc/data_04_12"
SELECTION_FILE = QUALITY_ROOT / "data_04_12_selection.json"
REPORT_FILE = QUALITY_ROOT / "data_04_12_technical_report.json"
WAREHOUSE_FILE = PROJECT_ROOT / "data/warehouse/moroccan_markets.duckdb"
SCHEMA_VERSION = "1.0"
MAX_DOCUMENTS = 3


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def existing_processed_keys(reference_path: Path = REFERENCE_FILE) -> set[tuple[str, str]]:
    payload = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    return {
        (document["company_id"], str(document["exercise_year"]))
        for document in payload.get("documents", [])
    }


def _workload(row: dict[str, str]) -> tuple[int, int, int]:
    return (
        int(row["estimated_workload_units"]),
        int(row["page_count"]),
        int(row["actual_size_bytes"]),
    )


def select_documents(
    audit_rows: list[dict[str, str]],
    plan_rows: list[dict[str, str]],
    coverage_rows: list[dict[str, str]],
    processed: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    audits = {(row["company_id"], row["exercise_year"]): row for row in audit_rows}
    plans = {(row["company_id"], row["exercise_year"]): row for row in plan_rows}
    covered_companies = {
        row["company_id"]
        for row in coverage_rows
        if row.get("status") == "VERIFIED"
    }
    candidates = []
    for key, audit in audits.items():
        plan = plans.get(key)
        if (
            key in processed
            or not plan
            or audit.get("audit_status") != "SUCCESS"
            or audit.get("manifest_valid", "").lower() != "true"
        ):
            continue
        candidates.append({**audit, **plan})

    current_2025_companies = {
        company for company, year in processed if year == "2025"
    }
    comparison = min(
        (
            row
            for row in candidates
            if row["exercise_year"] == "2024"
            and row["company_id"] in current_2025_companies
        ),
        key=_workload,
    )
    remaining = [
        row
        for row in candidates
        if (row["company_id"], row["exercise_year"])
        != (comparison["company_id"], comparison["exercise_year"])
    ]
    historical = min(
        (
            row
            for row in remaining
            if row["exercise_year"] in {"2023", "2024"}
            and row["company_id"] in covered_companies
            and row["document_classification"] == "TEXT"
        ),
        key=_workload,
    )
    selected_companies = {comparison["company_id"], historical["company_id"]}
    newcomer = min(
        (
            row
            for row in remaining
            if row["company_id"] not in covered_companies
            and row["company_id"] not in selected_companies
            and row["document_classification"] == "TEXT"
        ),
        key=_workload,
    )
    selections = [comparison, historical, newcomer]
    reasons = [
        (
            "Rapport 2024 au plus faible workload parmi les entreprises déjà "
            "traitées en 2025; comparaison interannuelle prioritaire."
        ),
        (
            "Rapport historique TEXT d'une entreprise possédant déjà des "
            "observations VERIFIED, avec le risque OCR minimal."
        ),
        (
            "Nouveau company_id; plus faible workload parmi les nouveaux rapports "
            "TEXT afin d'élargir la couverture sectorielle."
        ),
    ]
    for order, (selection, reason) in enumerate(
        zip(selections, reasons, strict=True), start=1
    ):
        selection["selection_order"] = order
        selection["selection_reason"] = reason
        selection["selection_score"] = int(selection["estimated_workload_units"])
    return selections


def validate_selection(
    selections: list[dict[str, Any]],
    manifest_rows: list[dict[str, str]],
    *,
    max_documents: int = MAX_DOCUMENTS,
) -> list[dict[str, Any]]:
    if not selections or len(selections) > max_documents:
        raise ValueError(
            f"Le lot autorise entre un et {max_documents} documents"
        )
    manifest = {
        (row["company_id"], row["exercise_year"]): row for row in manifest_rows
    }
    validations = []
    for selection in selections:
        key = (selection["company_id"], selection["exercise_year"])
        entry = manifest.get(key)
        if entry is None:
            raise ValueError(f"Document absent du manifeste : {key}")
        source = (PROJECT_ROOT / selection["local_path"]).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        actual_sha256 = file_hash(source)
        actual_size = source.stat().st_size
        with pymupdf.open(source) as document:
            actual_pages = document.page_count
        issues = []
        if actual_sha256 != selection["sha256"] or actual_sha256 != entry["sha256"]:
            issues.append("SHA256_MISMATCH")
        if actual_size != int(selection["actual_size_bytes"]):
            issues.append("SIZE_MISMATCH")
        if actual_pages != int(selection["page_count"]):
            issues.append("PAGE_COUNT_MISMATCH")
        if entry["issuer"] != selection["issuer"]:
            issues.append("ISSUER_MISMATCH")
        if entry["exercise_year"] != selection["exercise_year"]:
            issues.append("EXERCISE_MISMATCH")
        if issues:
            raise ValueError(f"Validation préalable échouée pour {key}: {issues}")
        validations.append(
            {
                "company_id": selection["company_id"],
                "exercise_year": int(selection["exercise_year"]),
                "source_pdf": str(source),
                "sha256": actual_sha256,
                "size_bytes": actual_size,
                "page_count": actual_pages,
                "status": "PASSED",
            }
        )
    return validations


def protected_state() -> dict[str, Any]:
    source_root = PROJECT_ROOT / "src"
    streamlit_files = sorted(
        path
        for path in source_root.rglob("*.py")
        if "streamlit" in path.name.casefold()
        or "dashboard" in {part.casefold() for part in path.parts}
    )
    return {
        "warehouse": (
            file_hash(WAREHOUSE_FILE) if WAREHOUSE_FILE.is_file() else None
        ),
        "streamlit": {str(path): file_hash(path) for path in streamlit_files},
    }


def selection_payload(
    selections: list[dict[str, Any]],
    validations: list[dict[str, Any]],
    *,
    max_documents: int = MAX_DOCUMENTS,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "max_documents": max_documents,
        "selection_count": len(selections),
        "execution_order": [
            {
                "order": row["selection_order"],
                "company_id": row["company_id"],
                "issuer": row["issuer"],
                "exercise_year": int(row["exercise_year"]),
                "classification": row["document_classification"],
                "recommended_strategy": row["recommended_strategy"],
                "page_count": int(row["page_count"]),
                "size_bytes": int(row["actual_size_bytes"]),
                "ocr_candidate_pages": parse_page_list(row["ocr_candidate_pages"]),
                "financial_candidate_pages": parse_page_list(
                    row["financial_candidate_pages"]
                ),
                "estimated_workload_units": int(row["estimated_workload_units"]),
                "reason": row["selection_reason"],
                "local_path": row["local_path"],
                "sha256": row["sha256"],
            }
            for row in selections
        ],
        "preflight": validations,
        "inputs": {
            "manifest_sha256": file_hash(MANIFEST_FILE),
            "audit_sha256": file_hash(AUDIT_FILE),
            "plan_sha256": file_hash(PLAN_FILE),
            "coverage_sha256": file_hash(COVERAGE_FILE),
        },
    }


def must_stop(result: dict[str, Any]) -> tuple[bool, str | None]:
    if result.get("pipeline_status") == "FAILED":
        return True, "PIPELINE_FAILED"
    if int(result.get("peak_rss_bytes", 0)) >= MAX_SAFE_RSS_BYTES:
        return True, "MEMORY_LIMIT_EXCEEDED"
    if not result.get("artifacts_sha256"):
        return True, "PROVENANCE_ARTIFACTS_MISSING"
    metrics = result.get("metrics", {})
    if int(metrics.get("tables_missing_page_reference", 0)):
        return True, "TABLE_PAGE_PROVENANCE_MISSING"
    return False, None


def execute_batch(
    selections: list[dict[str, Any]],
    *,
    resume: bool,
    output_root: Path,
    continue_after_document_stop: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    results = []
    stop_reason = None
    for selection in selections:
        result_path = (
            output_root
            / f"{selection['company_id']}_{selection['exercise_year']}"
            / "pilot_result.json"
        )
        cached = reusable_result(result_path, selection) if resume else None
        if cached is not None:
            result = cached
        else:
            spec_path = result_path.parent / "worker_spec.json"
            atomic_json(spec_path, selection)
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "moroccan_markets_ai.data.processing.corpus_pipeline_pilot",
                    "--worker-spec",
                    str(spec_path),
                    "--output-dir",
                    str(output_root),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if not result_path.is_file():
                result = {
                    "company_id": selection["company_id"],
                    "exercise_year": int(selection["exercise_year"]),
                    "pipeline_status": "FAILED",
                    "error": f"Worker code {process.returncode}: {process.stderr[-2000:]}",
                    "artifacts_sha256": {},
                }
                atomic_json(result_path, result)
            else:
                result = json.loads(result_path.read_text(encoding="utf-8"))
        results.append(result)
        stop, stop_reason = must_stop(result)
        if stop:
            if continue_after_document_stop:
                result["batch_control"] = {
                    "document_stopped": True,
                    "reason": stop_reason,
                    "subsequent_documents_allowed": True,
                }
                atomic_json(result_path, result)
                stop_reason = None
                continue
            break
    return results, stop_reason


def run_batch(
    *,
    dry_run: bool = False,
    resume: bool = True,
    output_root: Path = OUTPUT_ROOT,
    quality_root: Path = QUALITY_ROOT,
) -> dict[str, Any]:
    audit_rows = read_csv(AUDIT_FILE)
    plan_rows = read_csv(PLAN_FILE)
    coverage_rows = read_csv(COVERAGE_FILE)
    manifest_rows = read_csv(MANIFEST_FILE)
    processed = existing_processed_keys()
    selections = select_documents(audit_rows, plan_rows, coverage_rows, processed)
    if {(row["company_id"], row["exercise_year"]) for row in selections} & processed:
        raise ValueError("La sélection contient un rapport déjà traité")
    validations = validate_selection(selections, manifest_rows)
    quality_root.mkdir(parents=True, exist_ok=True)
    selection = selection_payload(selections, validations)
    atomic_json(quality_root / SELECTION_FILE.name, selection)
    protected_before = protected_state()
    results: list[dict[str, Any]] = []
    stop_reason = None
    if not dry_run:
        results, stop_reason = execute_batch(
            selections, resume=resume, output_root=output_root
        )
    protected_after = protected_state()
    if protected_after != protected_before:
        raise RuntimeError("DuckDB ou Streamlit a été modifié pendant DATA-04.12")
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "DRY_RUN" if dry_run else "STOPPED" if stop_reason else "COMPLETED",
        "dry_run": dry_run,
        "resume": resume,
        "authorized_documents": [
            [row["company_id"], int(row["exercise_year"])] for row in selections
        ],
        "processed_document_count": len(results),
        "remaining_authorized_count": len(selections) - len(results),
        "stop_reason": stop_reason,
        "results": results,
        "totals": {
            "pages": sum(
                int(item.get("metrics", {}).get("pages_processed", 0))
                for item in results
            ),
            "elapsed_seconds": round(
                sum(float(item.get("elapsed_seconds", 0)) for item in results), 3
            ),
            "peak_rss_bytes": max(
                (int(item.get("peak_rss_bytes", 0)) for item in results), default=0
            ),
            "docling_tables": sum(
                int(item.get("metrics", {}).get("docling_tables_detected", 0))
                for item in results
            ),
            "ocr_tables": sum(
                int(item.get("metrics", {}).get("ocr_tables_detected", 0))
                for item in results
            ),
            "candidate_values": sum(
                int(item.get("metrics", {}).get("quality_values_total", 0))
                for item in results
            ),
        },
        "protected_state": protected_after,
        "manifest_sha256_after": file_hash(MANIFEST_FILE),
        "selection_file": str((quality_root / SELECTION_FILE.name).resolve()),
        "financial_validation_pending": not dry_run,
    }
    atomic_json(quality_root / REPORT_FILE.name, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--quality-root", type=Path, default=QUALITY_ROOT)
    args = parser.parse_args()
    report = run_batch(
        dry_run=args.dry_run,
        resume=args.resume,
        output_root=args.output_root,
        quality_root=args.quality_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
