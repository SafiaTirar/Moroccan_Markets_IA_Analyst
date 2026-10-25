from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pymupdf

from moroccan_markets_ai.data.parsers.document_integrator import file_hash, read_json
from moroccan_markets_ai.data.parsers.financial_quality_gate import (
    QualityRules,
    evaluate_quality,
    load_admissible_values,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REFERENCE_FILE = (
    PROJECT_ROOT / "config" / "quality" / "ammc" / "annual_pilot_references.json"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "quality" / "ammc" / "reference_pilot"
SCHEMA_VERSION = "1.0"
ALLOWED_SCOPES = {"SOCIAL", "CONSOLIDATED"}
ALLOWED_METHODS = {
    "DIRECT_PDF_TEXT_AND_VISUAL_COMPARISON",
    "DIRECT_PDF_VISUAL_COMPARISON",
}
UNIT_SCALES = {
    "MAD": Decimal(1),
    "DH": Decimal(1),
    "KMAD": Decimal(1000),
    "KDH": Decimal(1000),
    "MMAD": Decimal(1000000),
    "MDH": Decimal(1000000),
}
REQUIRED_OBSERVATION_FIELDS = {
    "reference_id",
    "indicator",
    "selector",
    "source_label",
    "currency",
    "scale",
    "accounting_perimeter",
    "period",
    "page_number",
    "verification_method",
    "verification_evidence",
    "expected_status",
}


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    temporary.replace(path)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char))
        .replace("’", "'")
        .split()
    )


def _decimal(value: Any) -> Decimal | None:
    compact = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if compact.startswith("(") and compact.endswith(")"):
        compact = "-" + compact[1:-1]
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(",", ".")
    try:
        return Decimal(compact)
    except InvalidOperation:
        return None


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()


def _page_text(document: pymupdf.Document, page_number: int) -> str:
    if not 1 <= page_number <= document.page_count:
        raise ValueError(f"Page de preuve hors PDF : {page_number}")
    return document[page_number - 1].get_text("text")


def _validate_evidence(
    document: pymupdf.Document, evidence: dict[str, Any], reference_id: str
) -> None:
    page_number = evidence.get("page_number")
    if not isinstance(page_number, int) or not 1 <= page_number <= document.page_count:
        raise ValueError(f"{reference_id}: page de preuve invalide")
    mode = evidence.get("mode")
    if mode == "TEXT_SEARCH":
        quote = evidence.get("quote")
        if not quote or _normalize(quote) not in _normalize(
            _page_text(document, page_number)
        ):
            raise ValueError(f"{reference_id}: preuve textuelle absente du PDF")
    elif mode == "VISUAL_REGION":
        bbox = evidence.get("bbox")
        if not (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) for value in bbox)
        ):
            raise ValueError(f"{reference_id}: coordonnées de revue absentes")
        page_rect = document[page_number - 1].rect
        x0, y0, x1, y1 = bbox
        if not (0 <= x0 < x1 <= page_rect.width and 0 <= y0 < y1 <= page_rect.height):
            raise ValueError(f"{reference_id}: coordonnées hors page")
    else:
        raise ValueError(f"{reference_id}: mode de preuve inconnu")


def _validate_context(
    document: pymupdf.Document,
    document_spec: dict[str, Any],
    observation: dict[str, Any],
) -> None:
    reference_id = observation["reference_id"]
    missing = REQUIRED_OBSERVATION_FIELDS - set(observation)
    if missing:
        raise ValueError(f"{reference_id}: champs manquants {sorted(missing)}")
    scope = observation["accounting_perimeter"]
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"{reference_id}: périmètre comptable invalide")
    if observation["verification_method"] not in ALLOWED_METHODS:
        raise ValueError(f"{reference_id}: méthode de vérification non fiable")
    expected_status = observation["expected_status"]
    unit = observation.get("unit", observation["currency"])
    expected_scale = UNIT_SCALES.get(unit)
    unresolved_unit = (
        expected_status == "REVIEW_REQUIRED"
        and observation["currency"] in {None, "UNKNOWN"}
        and observation["scale"] is None
    )
    if not unresolved_unit:
        if observation["currency"] != "MAD":
            raise ValueError(f"{reference_id}: devise non prise en charge")
        if expected_scale is None or Decimal(str(observation["scale"])) != expected_scale:
            raise ValueError(f"{reference_id}: unité et facteur d'échelle incohérents")
    exercise_year = int(document_spec["exercise_year"])
    period_years = {
        int(year) for year in re.findall(r"(?:19|20)\d{2}", str(observation["period"]))
    }
    current_period = exercise_year in period_years
    comparative_period = (
        observation.get("comparative_period") is True
        and period_years == {exercise_year - 1}
    )
    if not (current_period or comparative_period):
        raise ValueError(f"{reference_id}: exercice et période incohérents")
    if observation["page_number"] not in observation["selector"].get("page", []):
        raise ValueError(f"{reference_id}: page et sélecteur incohérents")
    if observation["selector"].get("table_id") != observation.get("table_id"):
        raise ValueError(f"{reference_id}: table et sélecteur incohérents")
    if _normalize(observation["selector"].get("label", "")) != _normalize(
        observation["source_label"]
    ):
        raise ValueError(f"{reference_id}: indicateur et libellé source incohérents")
    for evidence in observation["verification_evidence"]:
        _validate_evidence(document, evidence, reference_id)

    if expected_status in {"VERIFIED", "REJECTED"}:
        required = {"reference_value", "normalized_value", "reliable"}
        if required - set(observation) or observation.get("reliable") is not True:
            raise ValueError(f"{reference_id}: référence fiable incomplète")
        reference_value = _decimal(observation["reference_value"])
        normalized_value = _decimal(observation["normalized_value"])
        if (
            reference_value is None
            or normalized_value is None
            or reference_value * expected_scale != normalized_value
        ):
            raise ValueError(f"{reference_id}: normalisation de référence invalide")
    elif expected_status != "REVIEW_REQUIRED":
        raise ValueError(f"{reference_id}: statut attendu invalide")


def _validate_document_evidence(
    document: pymupdf.Document, document_spec: dict[str, Any]
) -> None:
    evidence = document_spec.get("document_evidence", {})
    required = {"identity", "exercise", "scope", "unit"}
    if required - set(evidence):
        raise ValueError("Preuves documentaires incomplètes")
    for name, item in evidence.items():
        _validate_evidence(document, item, f"document:{name}")
    issuer = _normalize(document_spec["issuer"])
    if issuer not in _normalize(evidence["identity"].get("quote", "")):
        raise ValueError("La preuve d'identité ne désigne pas l'émetteur")
    if str(document_spec["exercise_year"]) not in evidence["exercise"].get(
        "quote", ""
    ):
        raise ValueError("La preuve d'exercice ne contient pas l'année attendue")
    scope_quote = _normalize(evidence["scope"].get("quote", ""))
    scopes = {item["accounting_perimeter"] for item in document_spec["observations"]}
    if "SOCIAL" in scopes and not re.search(r"sociaux|individuel", scope_quote):
        raise ValueError("La preuve ne justifie pas le périmètre social")
    if "CONSOLIDATED" in scopes and "consolide" not in scope_quote:
        raise ValueError("La preuve ne justifie pas le périmètre consolidé")
    unit_quote = _normalize(evidence["unit"].get("quote", ""))
    if not re.search(r"\b[mk]?mad\b|\b[mk]?dhs?\b|dirhams?", unit_quote):
        raise ValueError("La preuve ne justifie pas la devise MAD")


def _selector(observation: dict[str, Any]) -> dict[str, Any]:
    return dict(observation["selector"])


def _build_rules(document_spec: dict[str, Any]) -> QualityRules:
    contexts: dict[tuple[str, str, str], dict[str, Any]] = {}
    references: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for observation in document_spec["observations"]:
        selector = _selector(observation)
        table_id = observation["table_id"]
        context = {
            "table_id": table_id,
            "label": selector["label"],
            "column": selector["column"],
            "unit": observation.get("unit", observation["currency"]),
            "column_periods": {
                selector["column"]: observation["period"],
            },
            "scope": observation["accounting_perimeter"],
        }
        context_key = (table_id, selector["label"], selector["column"])
        existing = contexts.get(context_key)
        if existing and (
            existing["unit"] != context["unit"]
            or existing["scope"] != context["scope"]
        ):
            raise ValueError(f"Contextes incompatibles pour {table_id}")
        if existing:
            existing["column_periods"].update(context["column_periods"])
        else:
            contexts[context_key] = context

        common = {
            "name": observation["reference_id"],
            **selector,
            "reference_id": observation["reference_id"],
            "source": str(_resolve(document_spec["source_pdf"])),
            "unit": context["unit"],
            "period": observation["period"],
            "scope": observation["accounting_perimeter"],
        }
        if observation["expected_status"] == "REVIEW_REQUIRED":
            reviews.append(
                {
                    "name": observation["reference_id"],
                    "selector": selector,
                    "reference_id": observation["reference_id"],
                    "source": str(_resolve(document_spec["source_pdf"])),
                    "reliable": True,
                    "decision": "REVIEW_REQUIRED",
                    "justification": observation.get(
                        "review_reason", "La preuve source reste ambiguë."
                    ),
                    "page": observation["page_number"],
                }
            )
            continue
        references.append(
            {
                **common,
                "value": observation["reference_value"],
                "reliable": observation["reliable"],
                "mismatch_status": observation["expected_status"],
                "company_id": document_spec["company_id"],
                "exercise_year": document_spec["exercise_year"],
                "indicator": observation["indicator"],
                "currency": observation["currency"],
                "scale": observation["scale"],
                "normalized_value": observation["normalized_value"],
                "source_pdf_sha256": document_spec["source_pdf_sha256"],
                "page_number": observation["page_number"],
                "table_id": observation["table_id"],
                "source_label": observation["source_label"],
                "verification_evidence": observation["verification_evidence"],
                "verification_method": observation["verification_method"],
            }
        )
    return QualityRules(
        context_overrides=tuple(contexts.values()),
        references=tuple(references),
        review_decisions=tuple(reviews),
        downstream_allowed_statuses=("VERIFIED",),
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _find_annotation(
    annotations: list[dict[str, Any]], observation: dict[str, Any]
) -> dict[str, Any]:
    selector = observation["selector"]
    selected = [
        item
        for item in annotations
        if item["table_id"] == selector["table_id"]
        and _normalize(item["label"]) == _normalize(selector["label"])
        and item["column"].strip() == selector["column"].strip()
        and selector["page"][0] in item["page"]
    ]
    if len(selected) != 1:
        raise ValueError(
            f"{observation['reference_id']}: cellule candidate non unique ({len(selected)})"
        )
    return selected[0]


def run_reference_validation(
    reference_file: Path = DEFAULT_REFERENCE_FILE,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    reference_path = Path(reference_file).resolve()
    specification = read_json(reference_path)
    if specification.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Version du fichier de références incompatible")
    documents = specification.get("documents", [])
    selected_count = sum(len(item.get("observations", [])) for item in documents)
    if not documents or selected_count < 1:
        raise ValueError("Le fichier doit contenir au moins une observation")

    output_root = Path(output_root).resolve()
    observations_output: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    document_reports: list[dict[str, Any]] = []
    candidate_universe = 0
    reliable_references = 0

    for document_spec in documents:
        source_pdf = _resolve(document_spec["source_pdf"])
        integrated_path = _resolve(document_spec["integrated_document"])
        integrated = read_json(integrated_path)
        actual_sha = file_hash(source_pdf)
        if actual_sha != document_spec["source_pdf_sha256"]:
            raise ValueError(f"SHA-256 source incorrect : {source_pdf}")
        if integrated.get("source_sha256") != actual_sha:
            raise ValueError("Le document intégré ne correspond pas au PDF de référence")
        with pymupdf.open(source_pdf) as pdf:
            _validate_document_evidence(pdf, document_spec)
            for observation in document_spec["observations"]:
                _validate_context(pdf, document_spec, observation)
                if observation.get("reliable") is True:
                    reliable_references += 1

        rules = _build_rules(document_spec)
        quality = evaluate_quality(
            integrated_path,
            rules=rules,
            output_root=output_root / "quality_gate",
        )
        annotations = _load_jsonl(Path(quality["outputs"]["annotations"]))
        candidate_universe += len(annotations)
        for observation in document_spec["observations"]:
            candidate = _find_annotation(annotations, observation)
            actual_status = candidate["status"]
            if actual_status != observation["expected_status"]:
                raise ValueError(
                    f"{observation['reference_id']}: statut {actual_status}, "
                    f"attendu {observation['expected_status']}"
                )
            record = {
                "company_id": document_spec["company_id"],
                "exercise_year": document_spec["exercise_year"],
                "indicator": observation["indicator"],
                "raw_value": candidate["raw_value"],
                "normalized_value": (
                    observation.get("normalized_value")
                    if actual_status == "VERIFIED"
                    else None
                ),
                "currency": observation["currency"],
                "scale": observation["scale"],
                "accounting_perimeter": observation["accounting_perimeter"],
                "source_pdf_sha256": actual_sha,
                "page_number": observation["page_number"],
                "table_id": observation["table_id"],
                "source_label": observation["source_label"],
                "verification_status": actual_status,
                "verification_evidence": observation["verification_evidence"],
                "verification_method": observation["verification_method"],
                "reference_id": observation["reference_id"],
                "candidate_value_id": candidate["value_id"],
                "candidate_normalized_decimal": candidate["normalized_decimal"],
                "candidate_bbox": candidate["provenance"].get("bbox"),
                "comparison": (
                    "MATCH"
                    if actual_status == "VERIFIED"
                    else "MISMATCH"
                    if actual_status == "REJECTED"
                    else "UNRESOLVED"
                ),
                "downstream_eligible": candidate["downstream_eligible"],
                "reasons": candidate["reasons"],
            }
            observations_output.append(record)
            if actual_status != "VERIFIED":
                review_queue.append(record)

        admissible = load_admissible_values(Path(quality["outputs"]["annotations"]).parent / "quality_report.json")
        document_reports.append(
            {
                "company_id": document_spec["company_id"],
                "exercise_year": document_spec["exercise_year"],
                "source_pdf": str(source_pdf),
                "source_pdf_sha256": actual_sha,
                "quality_report": str(
                    Path(quality["outputs"]["annotations"]).parent / "quality_report.json"
                ),
                "quality_gate_cache_used": quality["cache_used"],
                "candidate_values": len(annotations),
                "sample_observations": len(document_spec["observations"]),
                "sample_admissible": sum(
                    item["company_id"] == document_spec["company_id"]
                    and item["exercise_year"] == document_spec["exercise_year"]
                    and item["downstream_eligible"]
                    for item in observations_output
                ),
                "quality_gate_total_admissible": len(admissible),
            }
        )

    counts = Counter(item["verification_status"] for item in observations_output)
    observations_path = output_root / "reference_observations.jsonl"
    verified_path = output_root / "verified_observations.jsonl"
    review_path = output_root / "review_queue.jsonl"
    snapshot_path = output_root / "reference_sample.json"
    coverage_path = output_root / "quality_gate_coverage.json"
    _atomic_jsonl(observations_path, observations_output)
    _atomic_jsonl(
        verified_path,
        [
            item
            for item in observations_output
            if item["verification_status"] == "VERIFIED"
        ],
    )
    _atomic_jsonl(review_path, review_queue)
    _atomic_json(snapshot_path, specification)
    coverage = {
        "candidate_universe_audited": candidate_universe,
        "sample_candidates_examined": len(observations_output),
        "reliable_references": reliable_references,
        "by_status": {
            status: counts[status]
            for status in ("VERIFIED", "REVIEW_REQUIRED", "REJECTED", "NOT_CHECKED")
        },
        "data05_admissible": sum(
            item["downstream_eligible"] for item in observations_output
        ),
        "allowed_statuses": ["VERIFIED"],
        "documents": document_reports,
    }
    _atomic_json(coverage_path, coverage)
    report = {
        "schema_version": SCHEMA_VERSION,
        "reference_file": str(reference_path),
        "reference_file_sha256": file_hash(reference_path),
        "candidate_universe_audited": candidate_universe,
        "summary": {
            "sample_candidates_examined": len(observations_output),
            "reliable_references": reliable_references,
            "verified": counts["VERIFIED"],
            "review_required": counts["REVIEW_REQUIRED"],
            "rejected": counts["REJECTED"],
            "not_checked": counts["NOT_CHECKED"],
            "data05_admissible": sum(
                item["downstream_eligible"] for item in observations_output
            ),
        },
        "downstream_contract": {
            "allowed_statuses": ["VERIFIED"],
            "ambiguous_values_are_facts": False,
        },
        "documents": document_reports,
        "concordance": observations_output,
        "outputs": {
            "reference_sample": str(snapshot_path),
            "observations": str(observations_path),
            "verified_observations": str(verified_path),
            "review_queue": str(review_path),
            "quality_gate_coverage": str(coverage_path),
        },
    }
    report_path = output_root / "concordance_report.json"
    _atomic_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validation financière de référence des pilotes AMMC"
    )
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCE_FILE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    report = run_reference_validation(args.references, args.output_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
