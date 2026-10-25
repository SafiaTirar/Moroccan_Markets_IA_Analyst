from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

from moroccan_markets_ai.data.parsers.document_integrator import read_json
from moroccan_markets_ai.data.processing.financial_reference_validation import (
    DEFAULT_REFERENCE_FILE,
    _atomic_json,
    _atomic_jsonl,
    _decimal,
    _load_jsonl,
    _normalize,
    run_reference_validation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REGISTRY = (
    PROJECT_ROOT
    / "config"
    / "quality"
    / "ammc"
    / "financial_indicator_registry.json"
)
DEFAULT_EXTENSION_CONFIG = (
    PROJECT_ROOT / "config" / "quality" / "ammc" / "validation_extension.json"
)
DEFAULT_REFERENCE_OUTPUT = (
    PROJECT_ROOT / "data" / "quality" / "ammc" / "reference_pilot"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "data" / "quality" / "ammc" / "validation_extension"
)
CORPUS_CONFIG = PROJECT_ROOT / "config" / "data" / "ammc_corpus.json"
PROCESSING_PLAN = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ammc"
    / "corpus_audit"
    / "annual_processing_plan.csv"
)
SCHEMA_VERSION = "1.0"


def validate_indicator_registry(registry: dict[str, Any]) -> dict[str, Any]:
    if registry.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Version du référentiel d'indicateurs incompatible")
    profiles = registry.get("profiles", {})
    indicators = registry.get("indicators", [])
    company_profiles = registry.get("company_profiles", {})
    ids = [item.get("indicator") for item in indicators]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Identifiants d'indicateurs absents ou dupliqués")
    for item in indicators:
        if not item.get("business_definition") or not item.get("synonyms"):
            raise ValueError(f"Définition incomplète : {item.get('indicator')}")
        unknown_profiles = set(item.get("applicable_profiles", [])) - set(profiles)
        if unknown_profiles:
            raise ValueError(f"Profils inconnus : {sorted(unknown_profiles)}")
    if set(company_profiles.values()) - set(profiles):
        raise ValueError("Une entreprise utilise un profil inconnu")
    revenue = next(item for item in indicators if item["indicator"] == "REVENUE")
    if not {"BANKING", "INSURANCE"}.issubset(revenue["forbidden_profiles"]):
        raise ValueError("REVENUE doit exclure les profils bancaire et assurance")
    pnb = next(
        item for item in indicators if item["indicator"] == "BANKING_NET_REVENUE"
    )
    if pnb["applicable_profiles"] != ["BANKING"]:
        raise ValueError("Le produit net bancaire doit rester spécifique aux banques")
    corpus_companies = {
        item["company_id"] for item in read_json(CORPUS_CONFIG)["companies"]
    }
    if set(company_profiles) != corpus_companies:
        raise ValueError("Le référentiel ne couvre pas exactement les 15 entreprises")
    return {
        "company_count": len(company_profiles),
        "indicator_count": len(indicators),
        "profile_count": len(profiles),
    }


def _indicator_match(
    label: str, profile: str, indicators: list[dict[str, Any]]
) -> dict[str, Any] | None:
    normalized = _normalize(label)
    matches = []
    for indicator in indicators:
        if profile not in indicator["applicable_profiles"]:
            continue
        excluded_terms = {
            _normalize(term) for term in indicator.get("excluded_label_terms", [])
        }
        if any(term in normalized for term in excluded_terms):
            continue
        for synonym in indicator["synonyms"]:
            candidate = _normalize(synonym)
            if candidate and candidate in normalized:
                matches.append((len(candidate), indicator))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _priority_record(
    annotation: dict[str, Any],
    company_id: str,
    exercise_year: int,
    profile: str,
    indicator: dict[str, Any],
    source_pdf: str,
    existing_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    score = min(20, int(indicator["priority"]) // 5) + 30
    components = {"indicator_label": 30, "business_priority": score - 30}
    missing: list[str] = []
    for field, points in (
        ("period", 15),
        ("reporting_scope", 15),
        ("unit", 15),
    ):
        value = annotation.get(field)
        if value not in {None, "", "UNKNOWN"}:
            score += points
            components[field] = points
        else:
            missing.append(field)
    if annotation.get("page"):
        score += 10
        components["page_reference"] = 10
    else:
        missing.append("page")
    if annotation.get("table_id"):
        score += 10
        components["table_reference"] = 10
    else:
        missing.append("table_id")
    if annotation.get("provenance", {}).get("bbox"):
        score += 5
        components["coordinates"] = 5
    if existing_reference:
        score += 5
        components["existing_review_evidence"] = 5
    error_count = sum(
        reason.get("severity") == "ERROR" for reason in annotation.get("reasons", [])
    )
    warning_count = len(annotation.get("reasons", [])) - error_count
    penalty = error_count * 25 + warning_count * 10
    score = max(0, score - penalty)
    if penalty:
        components["anomaly_penalty"] = -penalty
    potential = (
        "HIGH"
        if score >= 85 and not missing
        else "MEDIUM"
        if score >= 60
        else "LOW"
    )
    return {
        "company_id": company_id,
        "exercise_year": exercise_year,
        "sector_profile": profile,
        "indicator": indicator["indicator"],
        "indicator_definition": indicator["business_definition"],
        "label": annotation["label"],
        "raw_value": annotation["raw_value"],
        "candidate_normalized_decimal": annotation["normalized_decimal"],
        "unit": annotation.get("unit"),
        "period": annotation.get("period"),
        "accounting_perimeter": annotation.get("reporting_scope"),
        "page": annotation.get("page", []),
        "table_id": annotation["table_id"],
        "value_id": annotation["value_id"],
        "current_status": annotation["status"],
        "source_pdf": source_pdf,
        "coordinates": annotation.get("provenance", {}).get("bbox"),
        "reasons": annotation.get("reasons", []),
        "priority_score": score,
        "score_components": components,
        "validation_potential": potential,
        "missing_requirements": missing,
        "score_grants_verified": False,
    }


def build_priority_queue(
    reference_report: dict[str, Any], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    indicators = registry["indicators"]
    profiles = registry["company_profiles"]
    reference_by_value = {
        item["candidate_value_id"]: item
        for item in reference_report["concordance"]
    }
    candidates: list[dict[str, Any]] = []
    for document in reference_report["documents"]:
        company_id = document["company_id"]
        profile = profiles[company_id]
        quality = read_json(Path(document["quality_report"]))
        annotations = _load_jsonl(Path(quality["outputs"]["annotations"]))
        for annotation in annotations:
            if annotation["status"] in {"VERIFIED", "REJECTED"}:
                continue
            indicator = _indicator_match(annotation["label"], profile, indicators)
            if indicator is None:
                continue
            candidates.append(
                _priority_record(
                    annotation,
                    company_id,
                    document["exercise_year"],
                    profile,
                    indicator,
                    document["source_pdf"],
                    reference_by_value.get(annotation["value_id"]),
                )
            )

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            candidate["company_id"],
            candidate["exercise_year"],
            candidate["indicator"],
            candidate["period"],
            candidate["accounting_perimeter"],
            candidate["unit"],
            candidate["candidate_normalized_decimal"],
        )
        groups.setdefault(key, []).append(candidate)
    queue = []
    for occurrences in groups.values():
        best = max(occurrences, key=lambda item: item["priority_score"])
        item = dict(best)
        item["occurrence_count"] = len(occurrences)
        item["occurrences"] = [
            {
                "value_id": occurrence["value_id"],
                "page": occurrence["page"],
                "table_id": occurrence["table_id"],
                "raw_value": occurrence["raw_value"],
                "coordinates": occurrence["coordinates"],
            }
            for occurrence in occurrences
        ]
        queue.append(item)
    return sorted(
        queue,
        key=lambda item: (-item["priority_score"], item["company_id"], item["value_id"]),
    )


def _find_annotation(
    annotations: list[dict[str, Any]], selector: dict[str, Any]
) -> dict[str, Any] | None:
    matches = [
        item
        for item in annotations
        if item["table_id"] == selector["table_id"]
        and _normalize(item["label"]) == _normalize(selector["label"])
        and item["column"].strip() == selector["column"].strip()
    ]
    return matches[0] if len(matches) == 1 else None


def run_deterministic_checks(
    configuration: dict[str, Any], reference_report: dict[str, Any]
) -> list[dict[str, Any]]:
    references = {
        item["reference_id"]: item for item in reference_report["concordance"]
    }
    annotations_by_company = {}
    for document in reference_report["documents"]:
        quality = read_json(Path(document["quality_report"]))
        annotations_by_company[document["company_id"]] = _load_jsonl(
            Path(quality["outputs"]["annotations"])
        )
    results = []
    for check in configuration["deterministic_checks"]:
        result = {
            "check_id": check["check_id"],
            "kind": check["kind"],
            "company_id": check["company_id"],
            "period": check["period"],
            "scope": check["scope"],
            "verification_effect": "COMPLEMENTARY_ONLY",
            "creates_verified_status": False,
        }
        if check["kind"] == "BALANCE_EQUALITY":
            left = references.get(check["left_reference_id"])
            right = references.get(check["right_reference_id"])
            comparable = bool(
                left
                and right
                and left["currency"]
                and left["currency"] == right["currency"]
                and left["scale"] == right["scale"]
                and left["accounting_perimeter"] == right["accounting_perimeter"]
            )
            if not comparable:
                result.update(
                    status="NOT_CHECKED",
                    reason="Unité ou contexte incomplet ; égalité non appliquée.",
                )
            else:
                left_value = _decimal(left["raw_value"])
                right_value = _decimal(right["raw_value"])
                result.update(
                    status="PASS" if left_value == right_value else "FAIL",
                    left_value=str(left_value),
                    right_value=str(right_value),
                )
        elif check["kind"] == "OCCURRENCE_CONSISTENCY":
            found = []
            annotations = annotations_by_company[check["company_id"]]
            for selector in check["occurrences"]:
                annotation = _find_annotation(annotations, selector)
                value = _decimal(annotation["raw_value"]) if annotation else None
                if value is None:
                    found = []
                    break
                found.append(
                    {
                        "value_id": annotation["value_id"],
                        "raw_value": annotation["raw_value"],
                        "unit": selector["unit"],
                        "scale": selector["scale"],
                        "value_mad": str(value * Decimal(str(selector["scale"]))),
                        "page": annotation["page"],
                        "table_id": annotation["table_id"],
                    }
                )
            if len(found) < 2:
                result.update(
                    status="NOT_CHECKED",
                    reason="Occurrences insuffisantes ou non numériques.",
                )
            else:
                values = [Decimal(item["value_mad"]) for item in found]
                spread = max(values) - min(values)
                tolerance = Decimal(check["tolerance_mad"])
                result.update(
                    status="PASS" if spread <= tolerance else "FAIL",
                    occurrences=found,
                    spread_mad=str(spread),
                    tolerance_mad=str(tolerance),
                )
        else:
            raise ValueError(f"Contrôle déterministe inconnu : {check['kind']}")
        results.append(result)
    return results


def build_review_dossiers(
    reference_report: dict[str, Any], output_root: Path
) -> list[dict[str, Any]]:
    documents = {item["company_id"]: item for item in reference_report["documents"]}
    dossiers = []
    dossier_root = output_root / "review_dossiers"
    for observation in reference_report["concordance"]:
        if observation["verification_status"] == "VERIFIED":
            continue
        document = documents[observation["company_id"]]
        source_pdf = Path(document["source_pdf"])
        page_number = observation["page_number"]
        with pymupdf.open(source_pdf) as pdf:
            page_text = pdf[page_number - 1].get_text("text").strip()
        dossier = {
            "reference_id": observation["reference_id"],
            "human_approval": None,
            "human_review_required": True,
            "observation": observation,
            "source": {
                "pdf": str(source_pdf),
                "sha256": observation["source_pdf_sha256"],
                "page_number": page_number,
                "table_id": observation["table_id"],
                "candidate_bbox": observation.get("candidate_bbox"),
                "evidence": observation["verification_evidence"],
                "page_text_excerpt": page_text[:2000],
            },
            "ambiguity_reasons": observation["reasons"],
            "decision_options": ["VERIFY_WITH_NEW_REFERENCE", "KEEP_REVIEW", "REJECT"],
        }
        safe_name = re.sub(r"[^a-z0-9_-]+", "_", observation["reference_id"].lower())
        path = dossier_root / f"{safe_name}.json"
        _atomic_json(path, dossier)
        dossiers.append(
            {
                "reference_id": observation["reference_id"],
                "status": observation["verification_status"],
                "path": str(path),
            }
        )
    _atomic_json(output_root / "review_dossiers_index.json", dossiers)
    return dossiers


def build_next_reports_plan(
    extension_config: dict[str, Any], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    with PROCESSING_PLAN.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    row_index = {
        (row["company_id"], int(row["exercise_year"])): row for row in rows
    }
    plan = []
    for item in extension_config["next_reports"]:
        row = row_index[(item["company_id"], item["exercise_year"])]
        plan.append(
            {
                **item,
                "indicator_profile": registry["profiles"][item["sector_objective"]],
                "local_path": row["local_path"],
                "sha256": row["sha256"],
                "document_classification": row["document_classification"],
                "recommended_strategy": row["recommended_strategy"],
                "ocr_candidate_count": int(row["ocr_candidate_count"]),
                "estimated_workload_units": int(row["estimated_workload_units"]),
                "execution_authorized": False,
                "required_sequence": [
                    "Docling séquentiel avec cache",
                    "OCR ciblé uniquement sur les pages requises",
                    "constitution de références PDF",
                    "Quality Gate VERIFIED uniquement",
                ],
            }
        )
    return plan


def run_extension(
    registry_path: Path = DEFAULT_REGISTRY,
    extension_config_path: Path = DEFAULT_EXTENSION_CONFIG,
    reference_path: Path = DEFAULT_REFERENCE_FILE,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    registry = read_json(Path(registry_path))
    registry_summary = validate_indicator_registry(registry)
    extension_config = read_json(Path(extension_config_path))
    if extension_config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Version de configuration d'extension incompatible")
    reference_report = run_reference_validation(
        Path(reference_path), DEFAULT_REFERENCE_OUTPUT
    )
    output_root = Path(output_root).resolve()
    queue = build_priority_queue(reference_report, registry)
    checks = run_deterministic_checks(extension_config, reference_report)
    dossiers = build_review_dossiers(reference_report, output_root)
    next_plan = build_next_reports_plan(extension_config, registry)

    queue_path = output_root / "validation_queue.jsonl"
    checks_path = output_root / "deterministic_checks.json"
    plan_path = output_root / "next_three_reports_plan.json"
    _atomic_jsonl(queue_path, queue)
    _atomic_json(checks_path, checks)
    _atomic_json(plan_path, next_plan)
    status_counts = Counter(
        item["verification_status"] for item in reference_report["concordance"]
    )
    potential_counts = Counter(item["validation_potential"] for item in queue)
    report = {
        "schema_version": SCHEMA_VERSION,
        "registry": registry_summary,
        "candidate_universe": reference_report["candidate_universe_audited"],
        "sample_observations": len(reference_report["concordance"]),
        "quality_progress": {
            "baseline_verified": extension_config["baseline_verified"],
            "current_verified": status_counts["VERIFIED"],
            "new_verified": (
                status_counts["VERIFIED"] - extension_config["baseline_verified"]
            ),
            "review_required": status_counts["REVIEW_REQUIRED"],
            "rejected": status_counts["REJECTED"],
            "data05_admissible": reference_report["summary"]["data05_admissible"],
            "downstream_allowed_statuses": ["VERIFIED"],
        },
        "priority_queue": {
            "total": len(queue),
            "by_potential": dict(potential_counts),
            "score_can_verify": False,
        },
        "deterministic_checks": {
            "total": len(checks),
            "by_status": dict(Counter(item["status"] for item in checks)),
            "checks_are_standalone_proof": False,
        },
        "review_dossiers": len(dossiers),
        "next_reports_planned": len(next_plan),
        "mass_processing_started": False,
        "outputs": {
            "indicator_registry": str(Path(registry_path).resolve()),
            "priority_queue": str(queue_path),
            "deterministic_checks": str(checks_path),
            "review_dossiers_index": str(
                output_root / "review_dossiers_index.json"
            ),
            "next_reports_plan": str(plan_path),
            "reference_report": str(
                DEFAULT_REFERENCE_OUTPUT / "concordance_report.json"
            ),
        },
    }
    _atomic_json(output_root / "quality_gate_progress.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extension contrôlée de la validation financière AMMC"
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--config", type=Path, default=DEFAULT_EXTENSION_CONFIG)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCE_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    report = run_extension(
        args.registry, args.config, args.references, args.output_root
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
