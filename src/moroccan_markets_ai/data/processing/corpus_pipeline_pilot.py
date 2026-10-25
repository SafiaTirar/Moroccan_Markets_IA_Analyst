"""Pilote séquentiel Docling/OCR guidé par l'audit du corpus AMMC."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import resource
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from moroccan_markets_ai.data.parsers.document_integrator import (
    INTEGRATION_SCHEMA_VERSION,
    integrate,
)
from moroccan_markets_ai.data.parsers.financial_quality_gate import (
    QUALITY_SCHEMA_VERSION,
    QualityRules,
    evaluate_quality,
    file_hash,
)
from moroccan_markets_ai.data.parsers.ocr_recovery import OcrConfig, recover_document
from moroccan_markets_ai.data.parsers.pdf_parser import (
    EXTRACTION_CONFIG_SHA256,
    AMMCPDFParser,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import (
    AUDIT_FILE,
    PLAN_FILE,
    PROJECT_ROOT,
)
from moroccan_markets_ai.data.processing.corpus_pdf_audit import (
    OUTPUT_DIR as AUDIT_OUTPUT_DIR,
)

PILOT_SCHEMA_VERSION = "1.2"
SCOPE_SCHEMA_VERSION = "1.0"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "ammc" / "pipeline_pilot"
STRICT_QUALITY_ROOT = PROJECT_ROOT / "data" / "quality" / "ammc" / "pilot_strict"
REPORT_FILE = OUTPUT_DIR / "pilot_comparison_report.json"
MAX_SAFE_RSS_BYTES = 7 * 1024**3

SCOPE_PATTERNS = {
    "SOCIAL": (
        "comptes sociaux",
        "etats financiers sociaux",
        "etats de synthese sociaux",
        "comptes individuels",
    ),
    "CONSOLIDATED": (
        "comptes consolides",
        "comptes individuels et consolides",
        "etats financiers consolides",
        "etats de synthese consolides",
        "perimetre de consolidation",
    ),
}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join(normalized.encode("ascii", "ignore").decode().lower().split())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_page_list(value: str) -> list[int]:
    return sorted(
        {
            int(item)
            for item in value.replace(",", ";").split(";")
            if item.strip()
        }
    )


def selection_score(row: dict[str, str]) -> int:
    workload = int(row["estimated_workload_units"])
    size_mib = int(row.get("actual_size_bytes", "0")) / 1024**2
    return workload + math.ceil(size_mib * 2)


def select_pilots(
    audit_rows: list[dict[str, str]],
    plan_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    audits = {
        (row["company_id"], row["exercise_year"]): row for row in audit_rows
    }
    candidates = []
    for plan in plan_rows:
        audit = audits[(plan["company_id"], plan["exercise_year"])]
        if (
            plan["audit_status"] != "SUCCESS"
            or plan["manifest_valid"].lower() != "true"
        ):
            continue
        merged = {**audit, **plan}
        merged["base_selection_score"] = selection_score(merged)
        candidates.append(merged)

    selected = []
    for classification in ("TEXT", "MIXED"):
        options = [
            row for row in candidates if row["document_classification"] == classification
        ]
        if not options:
            raise ValueError(f"Aucun pilote {classification} disponible")
        for option in options:
            diversity_penalty = (
                25
                if selected and option["company_id"] == selected[0]["company_id"]
                else 0
            )
            option["selection_score"] = (
                option["base_selection_score"] + diversity_penalty
            )
        chosen = min(
            options,
            key=lambda row: (
                row["selection_score"],
                int(row["page_count"]),
                int(row["actual_size_bytes"]),
            ),
        )
        selected.append(chosen)
    return selected


def detect_reporting_scope(pdf_path: Path) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    with pymupdf.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            for raw_line in page.get_text("text").splitlines():
                line = " ".join(raw_line.split())
                normalized = normalize_text(line)
                if not normalized:
                    continue
                for scope, patterns in SCOPE_PATTERNS.items():
                    for pattern in patterns:
                        if pattern in normalized:
                            evidence.append(
                                {
                                    "scope": scope,
                                    "page": page_number,
                                    "matched_phrase": pattern,
                                    "text": line[:300],
                                }
                            )
                            break
    scopes = {item["scope"] for item in evidence}
    if len(scopes) > 1:
        document_scope = "MIXED"
    elif scopes:
        document_scope = next(iter(scopes))
    else:
        document_scope = "UNKNOWN"
    evidence_pages = {item["page"] for item in evidence}
    confidence = (
        "HIGH"
        if len(evidence_pages) >= 2
        else "MEDIUM"
        if evidence
        else "LOW"
    )
    return {
        "classification": document_scope,
        "confidence": confidence,
        "evidence": evidence,
        "caution": (
            "Le périmètre est attaché aux indices et pages; il ne doit pas être "
            "appliqué automatiquement à toutes les observations."
        ),
    }


def strict_quality_rules() -> QualityRules:
    return QualityRules(downstream_allowed_statuses=("VERIFIED",))


def extraction_strategy(selection: dict[str, Any]) -> str:
    """Return the execution order required by the reproducible audit plan."""
    recommended = str(selection.get("recommended_strategy", "")).upper()
    classification = str(selection.get("document_classification", "")).upper()
    if recommended == "OCR" or classification == "IMAGE":
        return "OCR_FIRST"
    return "DOCLING_FIRST"


def pipeline_fingerprint(selection: dict[str, Any]) -> str:
    payload = {
        "schema": PILOT_SCHEMA_VERSION,
        "scope_schema": SCOPE_SCHEMA_VERSION,
        "source_sha256": selection["sha256"],
        "extraction_strategy": extraction_strategy(selection),
        "ocr_pages": parse_page_list(selection["ocr_candidate_pages"]),
        "docling_config": EXTRACTION_CONFIG_SHA256,
        "ocr_config": OcrConfig().fingerprint,
        "integration_schema": INTEGRATION_SCHEMA_VERSION,
        "quality_schema": QUALITY_SCHEMA_VERSION,
        "quality_rules": strict_quality_rules().fingerprint,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def peak_rss_bytes() -> int:
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak = max(own, children)
    return int(peak if sys.platform == "darwin" else peak * 1024)


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _artifact_hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path.resolve()): file_hash(path) for path in paths if path.is_file()}


def run_one(selection: dict[str, Any], output_root: Path = OUTPUT_DIR) -> dict[str, Any]:
    started = time.monotonic()
    result_path = (
        output_root
        / f"{selection['company_id']}_{selection['exercise_year']}"
        / "pilot_result.json"
    )
    previous_result = None
    if result_path.is_file():
        try:
            previous_result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            previous_result = None
    pdf_path = (PROJECT_ROOT / selection["local_path"]).resolve()
    fingerprint = pipeline_fingerprint(selection)
    base: dict[str, Any] = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "pilot_fingerprint": fingerprint,
        "company_id": selection["company_id"],
        "issuer": selection["issuer"],
        "exercise_year": int(selection["exercise_year"]),
        "audit_classification": selection["document_classification"],
        "source_pdf": str(pdf_path),
        "source_sha256": selection["sha256"],
        "source_url": selection["pdf_url"],
        "detail_url": selection["detail_url"],
        "expected_pages": int(selection["page_count"]),
        "audit_ocr_pages": parse_page_list(selection["ocr_candidate_pages"]),
        "extraction_strategy": extraction_strategy(selection),
        "selection_score": int(selection["selection_score"]),
    }
    try:
        ocr = None
        ocr_elapsed = 0.0
        if base["extraction_strategy"] == "OCR_FIRST":
            ocr_started = time.monotonic()
            ocr = recover_document(pdf_path, pages=base["audit_ocr_pages"])
            ocr_elapsed += time.monotonic() - ocr_started

        parser = AMMCPDFParser()
        parse_started = time.monotonic()
        parsed = parser.parse(pdf_path)
        parse_elapsed = time.monotonic() - parse_started
        docling_metadata = Path(parsed.markdown_path).parent / "metadata.json"
        parser_ocr_pages = parsed.validation.ocr_required_pages
        requested_ocr_pages = sorted(
            set(base["audit_ocr_pages"]) | set(parser_ocr_pages)
        )

        if ocr is None or requested_ocr_pages != base["audit_ocr_pages"]:
            ocr_started = time.monotonic()
            ocr = recover_document(
                pdf_path,
                parser_metadata=docling_metadata,
                pages=requested_ocr_pages,
            )
            ocr_elapsed += time.monotonic() - ocr_started
        usable_ocr = (
            Path(ocr["source_pdf"]).resolve() == pdf_path
            and ocr.get("status") in {"PARSED", "PARTIAL"}
        )
        ocr_metadata_file = _find_ocr_metadata(pdf_path, ocr)

        integration_started = time.monotonic()
        integrated = integrate(
            docling_metadata=docling_metadata,
            ocr_metadata=ocr_metadata_file if usable_ocr else None,
        )
        integration_elapsed = time.monotonic() - integration_started
        integrated_path = (
            PROJECT_ROOT
            / "data"
            / "integrated"
            / "ammc"
            / f"{pdf_path.stem}_{selection['sha256'][:12]}"
            / "unified_document.json"
        )

        quality_started = time.monotonic()
        quality = evaluate_quality(
            integrated_path,
            rules=strict_quality_rules(),
            output_root=STRICT_QUALITY_ROOT,
        )
        quality_elapsed = time.monotonic() - quality_started
        annotations = jsonl(Path(quality["outputs"]["annotations"]))

        examined_pages = {
            int(item["page"])
            for item in [*ocr.get("tables", []), *ocr.get("rejected_regions", [])]
            if item.get("page") is not None
        }
        recovered_pages = {
            int(item["page"])
            for item in ocr.get("tables", [])
            if item.get("page") is not None
        }
        rejected_pages = {
            int(item["page"])
            for item in ocr.get("rejected_regions", [])
            if item.get("page") is not None
        }
        unverified_pages = {
            int(page)
            for annotation in annotations
            if annotation["status"] != "VERIFIED"
            for page in annotation.get("page", [])
        }
        ocr_ambiguous_pages = sorted(
            (set(requested_ocr_pages) - recovered_pages) | rejected_pages
        )
        ambiguous_pages = sorted(set(ocr_ambiguous_pages) | unverified_pages)
        docling_non_empty = (
            parsed.validation.valid_table_count
            + parsed.validation.incomplete_table_count
        )
        ocr_non_empty = len(ocr.get("tables", []))
        table_page_references = sum(
            bool(table.get("page")) for table in integrated.get("tables", [])
        )
        coordinate_references = sum(
            bool(table.get("coordinates_path"))
            for table in integrated.get("tables", [])
            if table.get("source") == "OCR"
        )
        scope = detect_reporting_scope(pdf_path)
        technical_success = integrated.get("active_table_count", 0) > 0
        pipeline_status = (
            "FAILED"
            if not technical_success
            else "PARTIAL"
            if (
                parsed.status.value != "PARSED"
                or ocr_ambiguous_pages
                or quality["status"] != "VERIFIED"
            )
            else "SUCCESS"
        )
        artifact_paths = [
            docling_metadata,
            ocr_metadata_file,
            integrated_path,
            Path(quality["outputs"]["annotations"]).parent / "quality_report.json",
        ]
        current_peak = peak_rss_bytes()
        prior_peak = (
            int(previous_result.get("peak_rss_bytes", 0))
            if previous_result
            and previous_result.get("source_sha256") == selection["sha256"]
            else 0
        )
        result = {
            **base,
            "pipeline_status": pipeline_status,
            "error": None,
            "stages": {
                "execution": {
                    "strategy": base["extraction_strategy"],
                    "order": (
                        ["ocr", "docling", "integration", "quality_gate"]
                        if base["extraction_strategy"] == "OCR_FIRST"
                        else ["docling", "ocr", "integration", "quality_gate"]
                    ),
                },
                "docling": {
                    "status": parsed.status.value,
                    "elapsed_seconds": round(parse_elapsed, 3),
                    "conversion_seconds": parsed.conversion_seconds,
                    "metadata": str(docling_metadata.resolve()),
                },
                "ocr": {
                    "status": ocr.get("status"),
                    "cache_used": ocr.get("cache_used", False),
                    "elapsed_seconds": round(ocr_elapsed, 3),
                    "duration_seconds": ocr.get("duration_seconds"),
                    "metadata": str(ocr_metadata_file.resolve()),
                    "audit_requested_pages": base["audit_ocr_pages"],
                    "parser_requested_pages": parser_ocr_pages,
                    "effective_requested_pages": requested_ocr_pages,
                },
                "integration": {
                    "status": integrated.get("integration_status"),
                    "cache_used": integrated.get("cache_used", False),
                    "elapsed_seconds": round(integration_elapsed, 3),
                    "artifact": str(integrated_path.resolve()),
                },
                "quality_gate": {
                    "status": quality["status"],
                    "cache_used": quality.get("cache_used", False),
                    "elapsed_seconds": round(quality_elapsed, 3),
                    "strict_verified_only": True,
                    "report": str(
                        (
                            Path(quality["outputs"]["annotations"]).parent
                            / "quality_report.json"
                        ).resolve()
                    ),
                },
            },
            "metrics": {
                "pages_processed": parsed.page_count,
                "pages_failed": max(base["expected_pages"] - parsed.page_count, 0),
                "docling_tables_detected": parsed.table_count,
                "docling_tables_non_empty": docling_non_empty,
                "docling_invalid_tables": parsed.validation.invalid_table_count,
                "audit_ocr_pages_requested": len(base["audit_ocr_pages"]),
                "parser_ocr_pages_requested": len(parser_ocr_pages),
                "ocr_pages_requested": len(requested_ocr_pages),
                "ocr_pages_processed": len(examined_pages),
                "ocr_pages_failed": len(
                    set(requested_ocr_pages) - examined_pages
                ),
                "ocr_tables_detected": len(ocr.get("tables", [])),
                "ocr_tables_non_empty": ocr_non_empty,
                "integrated_active_tables": integrated.get("active_table_count", 0),
                "ambiguous_pages": ambiguous_pages,
                "ocr_ambiguous_pages": ocr_ambiguous_pages,
                "financially_unverified_pages": sorted(unverified_pages),
                "table_page_references": table_page_references,
                "tables_missing_page_reference": (
                    integrated.get("active_table_count", 0) - table_page_references
                ),
                "ocr_coordinate_references": coordinate_references,
                "quality_values_total": quality["summary"]["total_values"],
                "quality_values_verified": quality["summary"]["by_status"][
                    "VERIFIED"
                ],
                "quality_values_review_required": quality["summary"]["by_status"][
                    "REVIEW_REQUIRED"
                ],
                "quality_values_rejected": quality["summary"]["by_status"][
                    "REJECTED"
                ],
                "quality_values_not_checked": quality["summary"]["by_status"][
                    "NOT_CHECKED"
                ],
                "data05_admissible_values": quality["summary"]["admissible_values"],
            },
            "reporting_scope": scope,
            "peak_rss_bytes": max(current_peak, prior_peak),
            "peak_rss_measurement": (
                "maximum of current run and prior full run for this source"
                if prior_peak
                else "current isolated worker"
            ),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "measured_component_seconds": round(
                parsed.conversion_seconds
                + float(ocr.get("duration_seconds", 0))
                + integration_elapsed
                + quality_elapsed,
                3,
            ),
            "artifacts_sha256": _artifact_hashes(artifact_paths),
        }
    except Exception as exc:  # noqa: BLE001 - isolation du pilote par document
        result = {
            **base,
            "pipeline_status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "peak_rss_bytes": peak_rss_bytes(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "artifacts_sha256": {},
        }
    atomic_json(result_path, result)
    return result


def _find_ocr_metadata(pdf_path: Path, ocr: dict[str, Any]) -> Path:
    root = PROJECT_ROOT / "data" / "extracted" / "ammc" / "ocr_generic"
    candidate = (
        root
        / f"{pdf_path.stem}_{ocr['source_sha256'][:12]}"
        / str(ocr["config_sha256"])[:12]
        / "ocr_metadata.json"
    )
    if not candidate.is_file():
        raise FileNotFoundError(f"Métadonnées OCR introuvables : {candidate}")
    return candidate


def reusable_result(path: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        result.get("pilot_fingerprint") != pipeline_fingerprint(selection)
        or result.get("pipeline_status") == "FAILED"
    ):
        return None
    pdf_path = Path(result.get("source_pdf", ""))
    if not pdf_path.is_file() or file_hash(pdf_path) != result.get("source_sha256"):
        return None
    for artifact, expected in result.get("artifacts_sha256", {}).items():
        path_item = Path(artifact)
        if not path_item.is_file() or file_hash(path_item) != expected:
            return None
    result["pilot_cache_used"] = True
    return result


def recommendation(results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    if any(result["pipeline_status"] == "FAILED" for result in results):
        return "NO-GO", ["Au moins un pipeline pilote a échoué techniquement."]
    if any(result["peak_rss_bytes"] >= MAX_SAFE_RSS_BYTES for result in results):
        return "NO-GO", ["Le pic mémoire observé ne laisse pas de marge sur 8 Go."]
    reservations = []
    if any(result["pipeline_status"] == "PARTIAL" for result in results):
        reservations.append("Au moins un pilote conserve un statut documentaire partiel.")
    if any(result["metrics"]["ambiguous_pages"] for result in results):
        reservations.append("Des pages restent ambiguës après Docling/OCR.")
    if any(result["metrics"]["data05_admissible_values"] == 0 for result in results):
        reservations.append(
            "Aucune valeur non vérifiée n'est autorisée vers DATA-05; des "
            "références fiables restent à configurer."
        )
    if any(
        result["metrics"]["tables_missing_page_reference"]
        for result in results
    ):
        reservations.append("Des tableaux intégrés n'ont pas de page source.")
    return ("GO AVEC RÉSERVES", reservations) if reservations else ("GO", [])


def build_report(
    selections: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Any]:
    decision, reasons = recommendation(results)
    return {
        "schema_version": PILOT_SCHEMA_VERSION,
        "execution_policy": {
            "max_parallel_documents": 1,
            "selected_document_count": 2,
            "mass_batch_started": False,
            "docling_used": True,
            "targeted_ocr_used": True,
            "qwen_or_duckdb_used": False,
        },
        "selection_method": {
            "formula": "audit workload + ceil(2 * size MiB) + diversity penalty",
            "diversity_penalty": 25,
            "classes": ["TEXT", "MIXED"],
        },
        "selections": [
            {
                "company_id": item["company_id"],
                "issuer": item["issuer"],
                "exercise_year": int(item["exercise_year"]),
                "classification": item["document_classification"],
                "page_count": int(item["page_count"]),
                "size_bytes": int(item["actual_size_bytes"]),
                "ocr_candidate_pages": parse_page_list(item["ocr_candidate_pages"]),
                "selection_score": int(item["selection_score"]),
                "local_path": item["local_path"],
                "source_url": item["pdf_url"],
                "sha256": item["sha256"],
            }
            for item in selections
        ],
        "results": results,
        "totals": {
            "documents": len(results),
            "pipeline_statuses": dict(
                Counter(result["pipeline_status"] for result in results)
            ),
            "elapsed_seconds": round(
                sum(float(result["elapsed_seconds"]) for result in results), 3
            ),
            "measured_component_seconds": round(
                sum(
                    float(result.get("measured_component_seconds", 0))
                    for result in results
                ),
                3,
            ),
            "peak_rss_bytes": max(
                (int(result["peak_rss_bytes"]) for result in results), default=0
            ),
            "pages_processed": sum(
                int(result.get("metrics", {}).get("pages_processed", 0))
                for result in results
            ),
            "ocr_pages_processed": sum(
                int(result.get("metrics", {}).get("ocr_pages_processed", 0))
                for result in results
            ),
            "tables_detected": sum(
                int(result.get("metrics", {}).get("docling_tables_detected", 0))
                + int(result.get("metrics", {}).get("ocr_tables_detected", 0))
                for result in results
            ),
            "data05_admissible_values": sum(
                int(result.get("metrics", {}).get("data05_admissible_values", 0))
                for result in results
            ),
        },
        "recommendation": decision,
        "recommendation_reasons": reasons,
        "financial_validation_warning": (
            "Un tableau techniquement détecté n'est pas financièrement validé. "
            "Le pilote applique un contrat downstream VERIFIED uniquement."
        ),
    }


def run_pilots(
    *,
    audit_dir: Path = AUDIT_OUTPUT_DIR,
    output_dir: Path = OUTPUT_DIR,
    resume: bool = True,
) -> dict[str, Any]:
    audit_rows = read_csv(audit_dir / AUDIT_FILE.name)
    plan_rows = read_csv(audit_dir / PLAN_FILE.name)
    selections = select_pilots(audit_rows, plan_rows)
    results = []
    for selection in selections:
        result_path = (
            output_dir
            / f"{selection['company_id']}_{selection['exercise_year']}"
            / "pilot_result.json"
        )
        cached = reusable_result(result_path, selection) if resume else None
        if cached is not None:
            results.append(cached)
            continue
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
                str(output_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if not result_path.is_file():
            result = {
                "pipeline_status": "FAILED",
                "company_id": selection["company_id"],
                "exercise_year": int(selection["exercise_year"]),
                "elapsed_seconds": 0,
                "peak_rss_bytes": 0,
                "error": (
                    f"Worker code {process.returncode}: "
                    f"{process.stderr[-2000:]}"
                ),
            }
            atomic_json(result_path, result)
        else:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if process.returncode and result.get("pipeline_status") != "FAILED":
                result["pipeline_status"] = "FAILED"
                result["error"] = f"Worker terminé avec le code {process.returncode}"
                atomic_json(result_path, result)
        results.append(result)
    for selection, result in zip(selections, results, strict=True):
        source_pdf = Path(result.get("source_pdf", ""))
        if source_pdf.is_file():
            result["reporting_scope"] = detect_reporting_scope(source_pdf)
            result_path = (
                output_dir
                / f"{selection['company_id']}_{selection['exercise_year']}"
                / "pilot_result.json"
            )
            atomic_json(result_path, result)
    report = build_report(selections, results)
    atomic_json(output_dir / REPORT_FILE.name, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=AUDIT_OUTPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--worker-spec", type=Path)
    args = parser.parse_args()
    if args.worker_spec:
        selection = json.loads(args.worker_spec.read_text(encoding="utf-8"))
        result = run_one(selection, args.output_dir)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1 if result["pipeline_status"] == "FAILED" else 0)
    report = run_pilots(
        audit_dir=args.audit_dir,
        output_dir=args.output_dir,
        resume=not args.no_resume,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
