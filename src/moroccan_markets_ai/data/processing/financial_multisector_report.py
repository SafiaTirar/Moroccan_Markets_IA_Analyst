"""Rapport DATA-04.11, matrice de couverture et export VERIFIED uniquement."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.document_integrator import read_json
from moroccan_markets_ai.data.processing.financial_reference_validation import (
    DEFAULT_REFERENCE_FILE,
    _atomic_json,
    _atomic_jsonl,
    _load_jsonl,
    run_reference_validation,
)
from moroccan_markets_ai.data.processing.financial_validation_extension import (
    DEFAULT_REGISTRY,
    _indicator_match,
    validate_indicator_registry,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = (
    PROJECT_ROOT / "config" / "quality" / "ammc" / "data_04_11.json"
)
PILOT_ROOT = PROJECT_ROOT / "data" / "processed" / "ammc" / "multisector_extension"
REFERENCE_ROOT = PROJECT_ROOT / "data" / "quality" / "ammc" / "reference_pilot"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "quality" / "ammc" / "data_04_11"
SCHEMA_VERSION = "1.0"
STATUS_PRIORITY = {
    "REJECTED": 4,
    "REVIEW_REQUIRED": 3,
    "VERIFIED": 2,
    "NOT_CHECKED": 1,
}


def processing_must_stop(
    results: list[dict[str, Any]], max_peak_rss_bytes: int
) -> bool:
    return any(
        result.get("pipeline_status") == "FAILED"
        or int(result.get("peak_rss_bytes", 0)) >= max_peak_rss_bytes
        or not result.get("artifacts_sha256")
        for result in results
    )


def load_pilot_results(
    target_order: list[str], exercise_year: int, root: Path = PILOT_ROOT
) -> list[dict[str, Any]]:
    results = []
    for company_id in target_order:
        path = root / f"{company_id}_{exercise_year}" / "pilot_result.json"
        if not path.is_file():
            raise FileNotFoundError(f"Résultat pilote absent : {path}")
        result = read_json(path)
        if result.get("company_id") != company_id:
            raise ValueError(f"Identité pilote incohérente : {path}")
        results.append(result)
    return results


def _combined_status(statuses: list[str]) -> str:
    unique = set(statuses)
    if len(unique) == 1:
        return next(iter(unique))
    if "REJECTED" in unique or "REVIEW_REQUIRED" in unique:
        return "REVIEW_REQUIRED"
    return max(unique, key=lambda status: STATUS_PRIORITY.get(status, 0))


def build_coverage_matrix(
    reference_report: dict[str, Any], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    references = reference_report["concordance"]
    rows = []
    for document in reference_report["documents"]:
        company_id = document["company_id"]
        exercise_year = int(document["exercise_year"])
        profile = registry["company_profiles"][company_id]
        quality = read_json(Path(document["quality_report"]))
        annotations = _load_jsonl(Path(quality["outputs"]["annotations"]))
        document_references = [
            item
            for item in references
            if item["company_id"] == company_id
            and int(item["exercise_year"]) == exercise_year
        ]
        perimeters = sorted(
            {
                item["accounting_perimeter"]
                for item in document_references
                if item["accounting_perimeter"] not in {None, "UNKNOWN"}
            }
            or {"UNKNOWN"}
        )
        reference_indicators = {
            item["indicator"] for item in document_references
        }
        indicators = list(registry["indicators"])
        known_ids = {item["indicator"] for item in indicators}
        indicators.extend(
            {
                "indicator": indicator,
                "business_definition": "Indicateur de référence hors référentiel sectoriel.",
                "applicable_profiles": [profile],
                "synonyms": [],
            }
            for indicator in sorted(reference_indicators - known_ids)
        )
        matched_annotations: dict[str, list[dict[str, Any]]] = {}
        for annotation in annotations:
            match = _indicator_match(annotation["label"], profile, indicators)
            if match:
                matched_annotations.setdefault(match["indicator"], []).append(
                    annotation
                )
        for perimeter in perimeters:
            for indicator in indicators:
                indicator_id = indicator["indicator"]
                applicable = profile in indicator["applicable_profiles"]
                selected = [
                    item
                    for item in document_references
                    if item["indicator"] == indicator_id
                    and item["accounting_perimeter"] == perimeter
                ]
                candidates = matched_annotations.get(indicator_id, [])
                if not applicable:
                    status = "NOT_APPLICABLE"
                    justification = "Indicateur non applicable au profil sectoriel."
                elif selected:
                    status = _combined_status(
                        [item["verification_status"] for item in selected]
                    )
                    justification = "Décision issue des références PDF contrôlées."
                elif candidates:
                    status = "NOT_CHECKED"
                    justification = (
                        "Candidat détecté, sans référence PDF fiable pour ce périmètre."
                    )
                else:
                    status = "NOT_AVAILABLE"
                    justification = "Aucun candidat correspondant détecté."
                rows.append(
                    {
                        "company_id": company_id,
                        "exercise_year": exercise_year,
                        "accounting_perimeter": perimeter,
                        "sector_profile": profile,
                        "indicator": indicator_id,
                        "status": status,
                        "candidate_count": len(candidates),
                        "reference_ids": [
                            item["reference_id"] for item in selected
                        ],
                        "justification": justification,
                    }
                )
    return rows


def verified_dataset(reference_report: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in reference_report["concordance"]:
        if item["verification_status"] != "VERIFIED":
            continue
        if not item["downstream_eligible"] or item["normalized_value"] is None:
            raise ValueError("Observation VERIFIED non conforme au contrat downstream")
        records.append(
            {
                "company_id": item["company_id"],
                "exercise_year": item["exercise_year"],
                "indicator": item["indicator"],
                "normalized_value": item["normalized_value"],
                "currency": item["currency"],
                "scale": item["scale"],
                "accounting_perimeter": item["accounting_perimeter"],
                "source_pdf_sha256": item["source_pdf_sha256"],
                "page_number": item["page_number"],
                "table_id": item["table_id"],
                "verification_reference_id": item["reference_id"],
                "verification_status": "VERIFIED",
            }
        )
    return records


def _atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fields = list(rows[0]) if rows else []
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)
    temporary.replace(path)


def run_report(
    config_path: Path = DEFAULT_CONFIG,
    registry_path: Path = DEFAULT_REGISTRY,
    reference_path: Path = DEFAULT_REFERENCE_FILE,
    output_root: Path = DEFAULT_OUTPUT,
    pilot_root: Path = PILOT_ROOT,
) -> dict[str, Any]:
    configuration = read_json(Path(config_path))
    if configuration.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Version de configuration DATA-04.11 incompatible")
    registry = read_json(Path(registry_path))
    validate_indicator_registry(registry)
    reference_report = run_reference_validation(reference_path, REFERENCE_ROOT)
    results = load_pilot_results(
        configuration["target_order"], configuration["exercise_year"], pilot_root
    )
    coverage = build_coverage_matrix(reference_report, registry)
    verified = verified_dataset(reference_report)
    output_root = Path(output_root).resolve()
    coverage_path = output_root / "financial_coverage_matrix.csv"
    verified_csv = output_root / "verified_financial_observations.csv"
    verified_jsonl = output_root / "verified_financial_observations.jsonl"
    _atomic_csv(coverage_path, coverage)
    _atomic_csv(verified_csv, verified)
    _atomic_jsonl(verified_jsonl, verified)
    stop = processing_must_stop(
        results, int(configuration["max_peak_rss_bytes"])
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "phase_a": {
            "groups_examined": len(configuration["reviewed_high_candidates"]),
            "decisions": configuration["reviewed_high_candidates"],
            "new_verified": sum(
                item["decision"] == "VERIFIED"
                for item in configuration["reviewed_high_candidates"]
            ),
        },
        "phase_b": {
            "documents": [
                {
                    "company_id": item["company_id"],
                    "status": item["pipeline_status"],
                    "elapsed_seconds": item["elapsed_seconds"],
                    "peak_rss_bytes": item["peak_rss_bytes"],
                    "stages": item.get("stages", {}),
                    "metrics": item.get("metrics", {}),
                    "reporting_scope": item.get("reporting_scope", {}),
                    "error": item.get("error"),
                }
                for item in results
            ],
            "processing_stopped": stop,
            "max_parallel_documents": 1,
            "new_verified": reference_report["summary"]["verified"]
            - configuration["baseline_verified"]
            - sum(
                item["decision"] == "VERIFIED"
                for item in configuration["reviewed_high_candidates"]
            ),
        },
        "quality_gate": {
            "candidate_universe": reference_report["candidate_universe_audited"],
            **reference_report["summary"],
            "downstream_allowed_statuses": ["VERIFIED"],
            "non_verified_leak_count": sum(
                item["verification_status"] != "VERIFIED" for item in verified
            ),
        },
        "coverage": {
            "rows": len(coverage),
            "by_status": dict(Counter(item["status"] for item in coverage)),
        },
        "recommendation": "NO-GO" if stop else "GO AVEC RÉSERVES",
        "outputs": {
            "coverage_matrix": str(coverage_path),
            "verified_csv": str(verified_csv),
            "verified_jsonl": str(verified_jsonl),
            "reference_report": str(REFERENCE_ROOT / "concordance_report.json"),
        },
        "qwen_or_duckdb_started": False,
        "other_corpus_reports_processed": 0,
    }
    _atomic_json(output_root / "data_04_11_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCE_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pilot-root", type=Path, default=PILOT_ROOT)
    args = parser.parse_args()
    report = run_report(
        args.config,
        args.registry,
        args.references,
        args.output_root,
        args.pilot_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
