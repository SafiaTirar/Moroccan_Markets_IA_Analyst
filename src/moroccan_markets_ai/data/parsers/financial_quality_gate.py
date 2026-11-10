from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.document_integrator import (
    file_hash,
    read_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "quality" / "ammc"
RULES_ROOT = PROJECT_ROOT / "config" / "quality" / "ammc"
QUALITY_SCHEMA_VERSION = "2.4"
QUALITY_STATUSES = (
    "VERIFIED",
    "REVIEW_REQUIRED",
    "REJECTED",
    "NOT_CHECKED",
)

PERIOD_RE = re.compile(
    r"(?:19|20)\d{2}|\b(?:S[12]|T[1-4])\b|\b\d{2}/\d{2}/\d{4}\b",
    re.IGNORECASE,
)
UNIT_RE = re.compile(r"\b(MDH|KDH|MAD|DH)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QualityRules:
    max_integer_digits: int = 15
    decimal_table_ratio: float = 0.7
    decimal_integer_min_digits: int = 7
    noisy_label_min_alpha_ratio: float = 0.45
    references: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    comparisons: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    subtotals: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    sign_rules: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    context_overrides: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    review_decisions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    downstream_allowed_statuses: tuple[str, ...] = (
        "VERIFIED",
        "NOT_CHECKED",
    )

    @property
    def fingerprint(self) -> str:
        payload = {"schema": QUALITY_SCHEMA_VERSION, **asdict(self)}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    @classmethod
    def from_json(cls, path: Path | None) -> QualityRules:
        if path is None:
            return cls()
        payload = read_json(Path(path))
        allowed = set(cls.__dataclass_fields__)
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "Règles de qualité inconnues : " + ", ".join(sorted(unknown))
            )
        for key in (
            "references",
            "comparisons",
            "subtotals",
            "sign_rules",
            "context_overrides",
            "review_decisions",
            "downstream_allowed_statuses",
        ):
            if key in payload:
                payload[key] = tuple(payload[key])
        return cls(**payload)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    body = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)


def _canonical_decimal(value: str) -> Decimal | None:
    compact = value.strip().replace("\u00a0", " ")
    if not compact or compact in {"-", "+", "—", "–"}:
        return None
    compact = compact.replace(" ", "")
    compact = compact.removesuffix("%")
    if compact.startswith("(") and compact.endswith(")"):
        compact = "-" + compact[1:-1]
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(",", ".")
    elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3}){2,}", compact):
        # Un point sépare parfois les milliers dans les états marocains.
        # Au moins deux groupes de trois chiffres évitent de confondre ce
        # format avec une valeur décimale ordinaire.
        compact = compact.replace(".", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", compact):
        return None
    try:
        return Decimal(compact)
    except InvalidOperation:
        return None


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _looks_numeric(value: str) -> bool:
    compact = value.strip().replace("\u00a0", " ")
    return bool(re.fullmatch(r"[(){}\[\]+\-–—.,%\d ]+", compact))


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _extract_periods(value: str) -> set[str]:
    periods = {match.upper() for match in PERIOD_RE.findall(value)}
    for year in re.findall(
        r"\b(?:30|31)[-/ ]?(?:D[ÉE]C)[\s./-]*(\d{2})\b",
        value,
        re.IGNORECASE,
    ):
        periods.add(f"20{year}")
    return periods


def _reason(code: str, message: str, severity: str = "WARNING") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _load_coordinates(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    coordinate_path = Path(path)
    if not coordinate_path.is_file():
        return []
    try:
        payload = read_json(coordinate_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return []
    rows = payload.get("rows", [])
    return rows if isinstance(rows, list) else []


def _cell_bbox(
    coordinate_rows: list[dict[str, Any]],
    row_index: int,
    column: str,
    raw_value: str,
) -> list[float] | None:
    if row_index >= len(coordinate_rows):
        return None
    cells = coordinate_rows[row_index].get("cells", [])
    exact = [
        cell
        for cell in cells
        if cell.get("column") == column and cell.get("value") == raw_value
    ]
    candidates = exact or [cell for cell in cells if cell.get("column") == column]
    bbox = candidates[0].get("bbox") if candidates else None
    return bbox if isinstance(bbox, list) else None


def _table_context(
    table: dict[str, Any],
    table_key: str,
    columns: list[str],
    labels: list[str],
    rules: QualityRules,
) -> dict[str, Any]:
    declared_unit = table.get("unit")
    units = {
        match.upper()
        for text in [*columns, *labels[:10]]
        for match in UNIT_RE.findall(text)
    }
    if declared_unit and declared_unit != "UNKNOWN":
        units.add(str(declared_unit).upper())
    column_periods = {}
    for column in columns:
        detected = _extract_periods(column)
        if len(detected) == 1:
            column_periods[column] = next(iter(detected))
    periods = set(column_periods.values())
    context = {
        "units": sorted(units),
        "periods": sorted(periods),
        "column_periods": column_periods,
        "reporting_scope": table.get("reporting_scope", "UNKNOWN"),
        "reference": None,
    }
    for override in rules.context_overrides:
        if override.get("label") is not None or override.get("column") is not None:
            continue
        if override.get("table_id") not in {None, table.get("table_id")}:
            continue
        if override.get("table_key") not in {None, table_key}:
            continue
        if override.get("page") is not None and override["page"] not in table.get(
            "page", []
        ):
            continue
        if unit := override.get("unit"):
            context["units"] = [unit]
        if override_periods := override.get("column_periods"):
            context["column_periods"].update(override_periods)
            context["periods"] = sorted(set(context["column_periods"].values()))
        elif override.get("periods"):
            context["periods"] = sorted(set(override["periods"]))
        if scope := override.get("scope"):
            context["reporting_scope"] = scope
        if override.get("reference"):
            context["reference"] = override["reference"]
    return context


def _cell_context(
    *,
    table: dict[str, Any],
    table_key: str,
    label: str,
    column: str,
    base_unit: str | None,
    base_period: str | None,
    base_scope: str,
    rules: QualityRules,
) -> tuple[str | None, str | None, str]:
    unit, period, scope = base_unit, base_period, base_scope
    for override in rules.context_overrides:
        if override.get("label") is None and override.get("column") is None:
            continue
        if override.get("table_id") not in {None, table.get("table_id")}:
            continue
        if override.get("table_key") not in {None, table_key}:
            continue
        if override.get("page") is not None and override["page"] not in table.get(
            "page", []
        ):
            continue
        if override.get("label") is not None and _normalized_label(
            override["label"]
        ) != _normalized_label(label):
            continue
        if override.get("column") is not None and str(
            override["column"]
        ).strip() != column.strip():
            continue
        if override_unit := override.get("unit"):
            unit = override_unit
        override_periods = override.get("column_periods", {})
        matching_periods = [
            value
            for key, value in override_periods.items()
            if str(key).strip() == column.strip()
        ]
        if len(matching_periods) == 1:
            period = matching_periods[0]
        elif len(override.get("periods", [])) == 1:
            period = override["periods"][0]
        if override_scope := override.get("scope"):
            scope = override_scope
    return unit, period, scope


def _structural_reasons(
    *,
    label: str,
    column: str,
    raw_value: str,
    row_values: list[str],
    decimal_ratio: float,
    rules: QualityRules,
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    digits = _digits(raw_value)
    if len(digits) > rules.max_integer_digits:
        reasons.append(
            _reason(
                "SUSPICIOUS_CONCATENATED_AMOUNT",
                "La cellule contient plus de chiffres que le seuil autorisé et "
                "peut regrouper plusieurs montants.",
                "ERROR",
            )
        )
    if not label.strip():
        reasons.append(
            _reason(
                "MISSING_ROW_LABEL",
                "La valeur chiffrée ne possède aucun libellé de ligne.",
                "ERROR",
            )
        )
    else:
        alphanumeric = re.findall(r"[A-Za-zÀ-ÿ0-9]", label)
        alphabetic = re.findall(r"[A-Za-zÀ-ÿ]", label)
        if (
            alphanumeric
            and len(alphabetic) / len(alphanumeric)
            < rules.noisy_label_min_alpha_ratio
        ):
            reasons.append(
                _reason(
                    "NOISY_ROW_LABEL",
                    "Le libellé contient trop peu de caractères alphabétiques.",
                )
            )
    if column.endswith("_extra"):
        reasons.append(
            _reason(
                "UNEXPECTED_EXTRA_COLUMN",
                "La valeur a été placée dans une colonne supplémentaire non "
                "justifiée par la structure détectée.",
                "ERROR",
            )
        )
    decimal_peer_digits = {
        _digits(peer)
        for peer in row_values
        if peer != raw_value and ("," in peer or "." in peer)
    }
    if digits and "," not in raw_value and "." not in raw_value:
        if digits in decimal_peer_digits:
            reasons.append(
                _reason(
                    "DECIMAL_SEPARATOR_AMBIGUITY",
                    "La même suite de chiffres existe dans la ligne avec un "
                    "séparateur décimal.",
                    "ERROR",
                )
            )
        elif (
            decimal_ratio >= rules.decimal_table_ratio
            and len(digits) >= rules.decimal_integer_min_digits
        ):
            reasons.append(
                _reason(
                    "INCONSISTENT_DECIMAL_FORMAT",
                    "Montant entier atypique dans un tableau majoritairement "
                    "décimal ; le séparateur peut avoir été perdu.",
                )
            )
    return reasons


def _annotation_status(reasons: list[dict[str, str]]) -> str:
    if any(reason["code"] == "UNPARSEABLE_AMOUNT" for reason in reasons):
        return "REJECTED"
    if reasons:
        return "REVIEW_REQUIRED"
    return "NOT_CHECKED"


def _find_annotations(
    annotations: list[dict[str, Any]], selector: dict[str, Any]
) -> list[dict[str, Any]]:
    matches = annotations
    if table_id := selector.get("table_id"):
        matches = [item for item in matches if item["table_id"] == table_id]
    if table_key := selector.get("table_key"):
        matches = [item for item in matches if item["table_key"] == table_key]
    if label := selector.get("label"):
        normalized = _normalized_label(label)
        matches = [
            item for item in matches if _normalized_label(item["label"]) == normalized
        ]
    if pattern := selector.get("label_pattern"):
        regex = re.compile(pattern, re.IGNORECASE)
        matches = [item for item in matches if regex.search(item["label"])]
    if "column" in selector and selector["column"] is not None:
        column = selector["column"]
        matches = [
            item
            for item in matches
            if item["column"].strip() == str(column).strip()
        ]
    if page := selector.get("page"):
        pages = page if isinstance(page, list) else [page]
        matches = [
            item for item in matches if any(value in item["page"] for value in pages)
        ]
    if status := selector.get("status"):
        matches = [item for item in matches if item["status"] == status]
    if reason_code := selector.get("reason_code"):
        matches = [
            item
            for item in matches
            if any(
                reason["code"] == reason_code for reason in item["reasons"]
            )
        ]
    return matches


def _quarantine(
    annotation: dict[str, Any],
    reason: dict[str, str],
) -> None:
    if reason not in annotation["reasons"]:
        annotation["reasons"].append(reason)
    if annotation["status"] != "REJECTED":
        annotation["status"] = "REVIEW_REQUIRED"
    annotation["downstream_eligible"] = False


def _apply_review_decisions(
    annotations: list[dict[str, Any]], rules: QualityRules
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, review in enumerate(rules.review_decisions, 1):
        name = review.get("name", f"review_{index}")
        selected = _find_annotations(annotations, review.get("selector", {}))
        reliable = review.get("reliable") is True and all(
            review.get(key) for key in ("reference_id", "source")
        )
        decision = review.get("decision")
        if not selected or not reliable or decision not in {
            "REJECTED",
            "REVIEW_REQUIRED",
        }:
            checks.append(
                {
                    "check_id": name,
                    "kind": "SOURCE_REVIEW",
                    "status": "NOT_CHECKED",
                    "outcome": "SKIPPED",
                    "justification": (
                        "Décision, référence fiable ou cellules applicables absentes."
                    ),
                }
            )
            continue
        for annotation in selected:
            annotation["status"] = decision
            annotation["downstream_eligible"] = False
            annotation["reasons"].append(
                _reason(
                    "SOURCE_REVIEW_REJECTED"
                    if decision == "REJECTED"
                    else "SOURCE_REVIEW_UNRESOLVED",
                    review.get(
                        "justification",
                        "La revue de la source confirme que la valeur ne peut pas "
                        "être admise telle quelle.",
                    ),
                    "ERROR" if decision == "REJECTED" else "WARNING",
                )
            )
            annotation["review"] = {
                "reference_id": review["reference_id"],
                "source": review["source"],
                "page": review.get("page"),
                "decision": decision,
            }
        checks.append(
            {
                "check_id": name,
                "kind": "SOURCE_REVIEW",
                "status": decision,
                "outcome": "CONFIRMED",
                "value_ids": [item["value_id"] for item in selected],
                "reference_id": review["reference_id"],
                "source": review["source"],
                "justification": review.get(
                    "justification",
                    "Décision confirmée contre la source indiquée.",
                ),
            }
        )
    return checks


def _apply_references(
    annotations: list[dict[str, Any]], rules: QualityRules
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, reference in enumerate(rules.references, 1):
        name = reference.get("name", f"reference_{index}")
        selected = _find_annotations(annotations, reference)
        reliable = reference.get("reliable") is True and all(
            reference.get(key)
            for key in (
                "reference_id",
                "source",
                "unit",
                "period",
                "scope",
            )
        )
        expected = _canonical_decimal(str(reference.get("value", "")))
        context_matches = (
            len(selected) == 1
            and selected[0].get("unit") == reference.get("unit")
            and selected[0].get("period") == reference.get("period")
            and selected[0].get("reporting_scope") == reference.get("scope")
        )
        if (
            len(selected) != 1
            or expected is None
            or not reliable
            or not context_matches
        ):
            checks.append(
                {
                    "check_id": name,
                    "kind": "REFERENCE",
                    "status": "NOT_CHECKED",
                    "outcome": "SKIPPED",
                    "justification": (
                        "Référence non fiable, valeur invalide ou cellule non "
                        "identifiée de manière unique."
                    ),
                }
            )
            continue
        annotation = selected[0]
        actual = Decimal(annotation["normalized_decimal"])
        if actual == expected and annotation["status"] != "REJECTED":
            annotation["status"] = "VERIFIED"
            annotation["downstream_eligible"] = True
            annotation["reference"] = {
                "reference_id": reference["reference_id"],
                "source": reference.get("source"),
                "expected_value": str(reference["value"]),
                "unit": reference["unit"],
                "period": reference["period"],
                "scope": reference["scope"],
            }
            for key in (
                "company_id",
                "exercise_year",
                "indicator",
                "currency",
                "scale",
                "normalized_value",
                "source_pdf_sha256",
                "page_number",
                "source_label",
                "verification_evidence",
                "verification_method",
            ):
                if key in reference:
                    annotation[key] = reference[key]
            annotation["accounting_perimeter"] = reference["scope"]
            annotation["verification_status"] = "VERIFIED"
            if annotation["reasons"]:
                annotation["resolved_reasons"] = [
                    reason["code"] for reason in annotation["reasons"]
                ]
            checks.append(
                {
                    "check_id": name,
                    "kind": "REFERENCE",
                    "status": "VERIFIED",
                    "outcome": "PASS",
                    "value_ids": [annotation["value_id"]],
                    "justification": "La valeur correspond à la référence fiable.",
                }
            )
        else:
            reason = _reason(
                "REFERENCE_MISMATCH",
                "La valeur extraite ne correspond pas à la référence fiable.",
                "ERROR",
            )
            _quarantine(annotation, reason)
            mismatch_status = reference.get(
                "mismatch_status", "REVIEW_REQUIRED"
            )
            if mismatch_status not in {"REVIEW_REQUIRED", "REJECTED"}:
                mismatch_status = "REVIEW_REQUIRED"
            annotation["status"] = mismatch_status
            annotation["verification_status"] = mismatch_status
            checks.append(
                {
                    "check_id": name,
                    "kind": "REFERENCE",
                    "status": mismatch_status,
                    "outcome": "FAIL",
                    "value_ids": [annotation["value_id"]],
                    "expected_value": str(reference["value"]),
                    "actual_value": annotation["raw_value"],
                    "justification": reason["message"],
                }
            )
    return checks


def _apply_comparisons(
    annotations: list[dict[str, Any]], rules: QualityRules
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, rule in enumerate(rules.comparisons, 1):
        name = rule.get("name", f"comparison_{index}")
        left = _find_annotations(annotations, rule.get("left", {}))
        right = _find_annotations(annotations, rule.get("right", {}))
        context_complete = all(
            rule.get(key) not in {None, "", "UNKNOWN"}
            for key in ("unit", "period", "scope")
        )
        if len(left) != 1 or len(right) != 1 or not context_complete:
            checks.append(
                {
                    "check_id": name,
                    "kind": rule.get("kind", "EQUALITY"),
                    "status": "NOT_CHECKED",
                    "outcome": "SKIPPED",
                    "justification": (
                        "Cellules non uniques ou unité, période et périmètre "
                        "insuffisamment définis."
                    ),
                }
            )
            continue
        tolerance = Decimal(str(rule.get("tolerance", "0")))
        difference = abs(
            Decimal(left[0]["normalized_decimal"])
            - Decimal(right[0]["normalized_decimal"])
        )
        passed = difference <= tolerance
        status = "NOT_CHECKED" if passed else "REVIEW_REQUIRED"
        check = {
            "check_id": name,
            "kind": rule.get("kind", "EQUALITY"),
            "status": status,
            "outcome": "PASS" if passed else "FAIL",
            "value_ids": [left[0]["value_id"], right[0]["value_id"]],
            "difference": str(difference),
            "unit": rule["unit"],
            "period": rule["period"],
            "scope": rule["scope"],
            "justification": (
                "Cohérence arithmétique observée ; elle ne constitue pas une "
                "validation contre une référence."
                if passed
                else "Les valeurs comparables ne concordent pas."
            ),
        }
        if not passed:
            reason = _reason(
                "FINANCIAL_COMPARISON_MISMATCH",
                f"Échec du contrôle {name}.",
                "ERROR",
            )
            _quarantine(left[0], reason)
            _quarantine(right[0], reason)
        checks.append(check)
    return checks


def _apply_subtotals(
    annotations: list[dict[str, Any]], rules: QualityRules
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, rule in enumerate(rules.subtotals, 1):
        name = rule.get("name", f"subtotal_{index}")
        total = _find_annotations(annotations, rule.get("total", {}))
        components = [
            _find_annotations(annotations, selector)
            for selector in rule.get("components", [])
        ]
        context_complete = all(
            rule.get(key) not in {None, "", "UNKNOWN"}
            for key in ("unit", "period", "scope")
        )
        if (
            len(total) != 1
            or not components
            or any(len(items) != 1 for items in components)
            or not context_complete
        ):
            checks.append(
                {
                    "check_id": name,
                    "kind": "SUBTOTAL",
                    "status": "NOT_CHECKED",
                    "outcome": "SKIPPED",
                    "justification": (
                        "Total, composants ou contexte financier incomplets."
                    ),
                }
            )
            continue
        component_items = [items[0] for items in components]
        component_sum = sum(
            (Decimal(item["normalized_decimal"]) for item in component_items),
            Decimal(0),
        )
        total_value = Decimal(total[0]["normalized_decimal"])
        tolerance = Decimal(str(rule.get("tolerance", "0")))
        difference = abs(total_value - component_sum)
        passed = difference <= tolerance
        if not passed:
            reason = _reason(
                "SUBTOTAL_MISMATCH",
                f"Le sous-total {name} ne correspond pas aux composants définis.",
                "ERROR",
            )
            _quarantine(total[0], reason)
            for item in component_items:
                _quarantine(item, reason)
        checks.append(
            {
                "check_id": name,
                "kind": "SUBTOTAL",
                "status": "NOT_CHECKED" if passed else "REVIEW_REQUIRED",
                "outcome": "PASS" if passed else "FAIL",
                "value_ids": [
                    total[0]["value_id"],
                    *(item["value_id"] for item in component_items),
                ],
                "difference": str(difference),
                "justification": (
                    "Sous-total arithmétiquement cohérent, sans validation "
                    "contre une référence."
                    if passed
                    else reason["message"]
                ),
            }
        )
    return checks


def _apply_sign_rules(
    annotations: list[dict[str, Any]], rules: QualityRules
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, rule in enumerate(rules.sign_rules, 1):
        name = rule.get("name", f"sign_{index}")
        selected = _find_annotations(annotations, rule.get("selector", {}))
        expected = rule.get("expected")
        if not selected or expected not in {"POSITIVE", "NON_NEGATIVE", "NEGATIVE"}:
            checks.append(
                {
                    "check_id": name,
                    "kind": "SIGN",
                    "status": "NOT_CHECKED",
                    "outcome": "SKIPPED",
                    "justification": "Règle de signe ou cellules applicables absentes.",
                }
            )
            continue
        failures = []
        for item in selected:
            value = Decimal(item["normalized_decimal"])
            valid = (
                (expected == "POSITIVE" and value > 0)
                or (expected == "NON_NEGATIVE" and value >= 0)
                or (expected == "NEGATIVE" and value < 0)
            )
            if not valid:
                failures.append(item)
                _quarantine(
                    item,
                    _reason(
                        "SIGN_MISMATCH",
                        f"Le signe ne respecte pas la règle {name}.",
                        "ERROR",
                    ),
                )
        checks.append(
            {
                "check_id": name,
                "kind": "SIGN",
                "status": "REVIEW_REQUIRED" if failures else "NOT_CHECKED",
                "outcome": "FAIL" if failures else "PASS",
                "value_ids": [item["value_id"] for item in selected],
                "justification": (
                    "Signe incohérent avec la règle configurée."
                    if failures
                    else "Signe cohérent ; aucune référence externe contrôlée."
                ),
            }
        )
    return checks


def _context_checks(table_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for table in table_reports:
        context = table["context"]
        for kind, values in (
            ("UNIT", context["units"]),
            ("PERIOD", context["periods"]),
        ):
            ambiguous = kind == "UNIT" and len(values) > 1
            checks.append(
                {
                    "check_id": f"{table['table_key']}:{kind.lower()}",
                    "kind": kind,
                    "status": "REVIEW_REQUIRED"
                    if ambiguous
                    else "NOT_CHECKED",
                    "outcome": (
                        "AMBIGUOUS"
                        if ambiguous
                        else "OBSERVED"
                        if values
                        else "SKIPPED"
                    ),
                    "table_key": table["table_key"],
                    "observed": values,
                    "justification": (
                        f"Plusieurs {kind.lower()}s incompatibles ont été détectées."
                        if ambiguous
                        else f"{kind.title()} observée mais non vérifiée."
                        if values
                        else f"{kind.title()} absente ou non identifiable."
                    ),
                }
            )
    return checks


def _default_financial_checks(rules: QualityRules) -> list[dict[str, Any]]:
    configured = {
        str(rule.get("kind", "EQUALITY")).upper()
        for rule in rules.comparisons
    }
    checks = []
    for kind in ("BALANCE", "NET_RESULT"):
        if kind not in configured:
            checks.append(
                {
                    "check_id": f"document:{kind.lower()}",
                    "kind": kind,
                    "status": "NOT_CHECKED",
                    "outcome": "SKIPPED",
                    "justification": (
                        "Aucune paire de cellules comparable avec même unité, "
                        "période et périmètre n'est configurée."
                    ),
                }
            )
    if not rules.subtotals:
        checks.append(
            {
                "check_id": "document:subtotals",
                "kind": "SUBTOTAL",
                "status": "NOT_CHECKED",
                "outcome": "SKIPPED",
                "justification": "Aucun groupe total/composants fiable n'est défini.",
            }
        )
    if not rules.sign_rules:
        checks.append(
            {
                "check_id": "document:signs",
                "kind": "SIGN",
                "status": "NOT_CHECKED",
                "outcome": "SKIPPED",
                "justification": (
                    "Aucune règle de signe n'est appliquée sans contexte métier "
                    "explicite."
                ),
            }
        )
    return checks


def _cache_result(
    report_path: Path,
    fingerprint: str,
) -> dict[str, Any] | None:
    if not report_path.is_file():
        return None
    try:
        report = read_json(report_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if report.get("quality_fingerprint") != fingerprint:
        return None
    root = report_path.parent
    for relative, expected in report.get("artifact_sha256", {}).items():
        path = root / relative
        if not path.is_file() or file_hash(path) != expected:
            return None
    report["cache_used"] = True
    return report


def evaluate_quality(
    integrated_document: Path,
    *,
    rules: QualityRules | None = None,
    rules_path: Path | None = None,
    output_root: Path = OUTPUT_ROOT,
) -> dict[str, Any]:
    integrated_path = Path(integrated_document).resolve()
    integrated = read_json(integrated_path)
    source_pdf = Path(integrated["source_pdf"])
    if rules is None:
        if rules_path is None:
            candidate = RULES_ROOT / f"{source_pdf.stem}.json"
            rules_path = candidate if candidate.is_file() else None
        rules = QualityRules.from_json(rules_path)
    source_sha = integrated["source_sha256"]
    if not source_pdf.is_file() or file_hash(source_pdf) != source_sha:
        raise ValueError("PDF source absent ou modifié")
    table_hashes: dict[str, str] = {}
    coordinate_hashes: dict[str, str] = {}
    for table in integrated.get("tables", []):
        csv_path = Path(table["csv_path"])
        actual_hash = file_hash(csv_path)
        if actual_hash != table.get("sha256"):
            raise ValueError(f"CSV intégré absent ou modifié : {csv_path}")
        table_hashes[str(csv_path)] = actual_hash
        if coordinate_path := table.get("coordinates_path"):
            coordinates = Path(coordinate_path)
            if not coordinates.is_file():
                raise ValueError(
                    f"Coordonnées OCR absentes : {coordinate_path}"
                )
            coordinates_hash = file_hash(coordinates)
            if coordinates_hash != table.get("coordinates_sha256"):
                raise ValueError(
                    f"Coordonnées OCR modifiées : {coordinate_path}"
                )
            coordinate_hashes[str(coordinates)] = coordinates_hash
    source_inputs = {
        "integrated_document": file_hash(integrated_path),
        "tables": table_hashes,
        "coordinates": coordinate_hashes,
    }
    fingerprint_payload = {
        "schema": QUALITY_SCHEMA_VERSION,
        "rules": rules.fingerprint,
        "sources": source_inputs,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode()
    ).hexdigest()
    output_dir = Path(output_root) / f"{source_pdf.stem}_{source_sha[:12]}"
    report_path = output_dir / "quality_report.json"
    cached = _cache_result(report_path, fingerprint)
    if cached is not None:
        return cached

    annotations: list[dict[str, Any]] = []
    table_reports: list[dict[str, Any]] = []
    seen_table_keys: set[str] = set()
    for table_index, table in enumerate(integrated.get("tables", []), 1):
        csv_path = Path(table["csv_path"])
        table_key = f"{table['source']}_{table_index:03d}_{csv_path.stem}"
        if table_key in seen_table_keys:
            raise ValueError(f"Identifiant de table dupliqué : {table_key}")
        seen_table_keys.add(table_key)
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            columns = reader.fieldnames or []
        label_column = "label" if "label" in columns else columns[0] if columns else None
        coordinate_rows = _load_coordinates(table.get("coordinates_path"))
        labels = [
            (row.get(label_column) or "").strip() if label_column else ""
            for row in rows
        ]
        numeric_values = [
            value
            for row in rows
            for column, value in row.items()
            if column != label_column
            and value
            and _canonical_decimal(value) is not None
        ]
        decimal_ratio = (
            sum("," in value or "." in value for value in numeric_values)
            / len(numeric_values)
            if numeric_values
            else 0.0
        )
        context = _table_context(table, table_key, columns, labels, rules)
        table_value_ids: list[str] = []
        for row_index, row in enumerate(rows):
            label = (
                (row.get(label_column) or "").strip() if label_column else ""
            )
            row_values = [
                value
                for column, value in row.items()
                if column != label_column and value
            ]
            label_periods = _extract_periods(label)
            for column, raw_value in row.items():
                if column == label_column or not raw_value:
                    continue
                amount = _canonical_decimal(raw_value)
                if amount is None:
                    if not _digits(raw_value) or not _looks_numeric(raw_value):
                        continue
                    reasons = [
                        _reason(
                            "UNPARSEABLE_AMOUNT",
                            "La cellule numérique ne peut pas être interprétée "
                            "sans correction.",
                            "ERROR",
                        )
                    ]
                else:
                    reasons = _structural_reasons(
                        label=label,
                        column=column,
                        raw_value=raw_value,
                        row_values=row_values,
                        decimal_ratio=decimal_ratio,
                        rules=rules,
                    )
                status = _annotation_status(reasons)
                value_id = f"{table_key}:r{row_index + 1}:{column}"
                base_unit = (
                    "%"
                    if raw_value.strip().endswith("%")
                    else context["units"][0]
                    if len(context["units"]) == 1
                    else None
                )
                base_period = (
                    next(iter(label_periods))
                    if len(label_periods) == 1
                    else context["column_periods"].get(column)
                ) or (
                    context["periods"][0]
                    if not context["column_periods"]
                    and len(context["periods"]) == 1
                    else None
                )
                unit, period, reporting_scope = _cell_context(
                    table=table,
                    table_key=table_key,
                    label=label,
                    column=column,
                    base_unit=base_unit,
                    base_period=base_period,
                    base_scope=context["reporting_scope"],
                    rules=rules,
                )
                annotation = {
                    "value_id": value_id,
                    "table_key": table_key,
                    "table_id": table["table_id"],
                    "source": table["source"],
                    "page": table.get("page", []),
                    "row_index": row_index + 1,
                    "csv_row_number": row_index + 2,
                    "label": label,
                    "column": column,
                    "raw_value": raw_value,
                    "normalized_decimal": str(amount) if amount is not None else None,
                    "unit": unit,
                    "period": period,
                    "reporting_scope": reporting_scope,
                    "status": status,
                    "downstream_eligible": status == "NOT_CHECKED",
                    "reasons": reasons,
                    "provenance": {
                        "csv_path": str(csv_path),
                        "csv_sha256": table["sha256"],
                        "coordinates_path": table.get("coordinates_path"),
                        "bbox": _cell_bbox(
                            coordinate_rows,
                            row_index,
                            column,
                            raw_value,
                        ),
                    },
                }
                annotations.append(annotation)
                table_value_ids.append(value_id)
        table_reports.append(
            {
                "table_key": table_key,
                "table_id": table["table_id"],
                "source": table["source"],
                "page": table.get("page", []),
                "csv_path": str(csv_path),
                "csv_sha256": table["sha256"],
                "context": context,
                "value_count": len(table_value_ids),
                "value_ids": table_value_ids,
            }
        )

    checks = _context_checks(table_reports)
    checks.extend(_default_financial_checks(rules))
    checks.extend(_apply_review_decisions(annotations, rules))
    checks.extend(_apply_references(annotations, rules))
    checks.extend(_apply_comparisons(annotations, rules))
    checks.extend(_apply_subtotals(annotations, rules))
    checks.extend(_apply_sign_rules(annotations, rules))

    allowed_statuses = set(rules.downstream_allowed_statuses)
    unknown_allowed = allowed_statuses - set(QUALITY_STATUSES)
    if not allowed_statuses or unknown_allowed:
        raise ValueError(
            "Statuts downstream invalides : "
            + ", ".join(sorted(unknown_allowed or allowed_statuses))
        )
    for annotation in annotations:
        annotation["downstream_eligible"] = (
            annotation["status"] in allowed_statuses
        )
    counts = Counter(item["status"] for item in annotations)
    admissible = [item for item in annotations if item["downstream_eligible"]]
    quarantine = [item for item in annotations if not item["downstream_eligible"]]
    strict_unverified_block = (
        bool(annotations)
        and not admissible
        and bool(counts["NOT_CHECKED"])
        and "NOT_CHECKED" not in allowed_statuses
    )
    document_status = (
        "REVIEW_REQUIRED"
        if strict_unverified_block
        else
        "REJECTED"
        if annotations and not admissible
        else "REVIEW_REQUIRED"
        if quarantine
        else "VERIFIED"
        if annotations and counts["VERIFIED"] == len(annotations)
        else "NOT_CHECKED"
    )

    annotations_path = output_dir / "annotations.jsonl"
    admissible_path = output_dir / "admissible_values.jsonl"
    quarantine_path = output_dir / "quarantine.jsonl"
    _atomic_jsonl(annotations_path, annotations)
    _atomic_jsonl(admissible_path, admissible)
    _atomic_jsonl(quarantine_path, quarantine)
    artifacts = {
        path.name: file_hash(path)
        for path in (annotations_path, admissible_path, quarantine_path)
    }
    report = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "source_pdf": str(source_pdf),
        "source_sha256": source_sha,
        "integrated_document": str(integrated_path),
        "integrated_document_sha256": source_inputs["integrated_document"],
        "rules": asdict(rules),
        "rules_sha256": rules.fingerprint,
        "quality_fingerprint": fingerprint,
        "status": document_status,
        "cache_used": False,
        "financial_validation": (
            "REFERENCE_VERIFIED"
            if annotations and counts["VERIFIED"] == len(annotations)
            else "PARTIAL_REFERENCE_CHECK"
            if counts["VERIFIED"]
            else "NOT_PERFORMED"
        ),
        "summary": {
            "total_values": len(annotations),
            "admissible_values": len(admissible),
            "non_admissible_values": len(quarantine),
            "by_status": {status: counts[status] for status in QUALITY_STATUSES},
        },
        "tables": table_reports,
        "checks": checks,
        "outputs": {
            "annotations": str(annotations_path.resolve()),
            "admissible_values": str(admissible_path.resolve()),
            "quarantine": str(quarantine_path.resolve()),
        },
        "downstream_contract": {
            "input": str(admissible_path.resolve()),
            "allowed_statuses": sorted(allowed_statuses),
            "forbidden_statuses": sorted(
                set(QUALITY_STATUSES) - allowed_statuses
            ),
            "not_checked_is_verified": False,
        },
        "artifact_sha256": artifacts,
    }
    _atomic_json(report_path, report)
    return report


def load_admissible_values(report_path: Path) -> list[dict[str, Any]]:
    report_file = Path(report_path).resolve()
    report = read_json(report_file)
    root = report_file.parent
    for relative, expected in report.get("artifact_sha256", {}).items():
        artifact = root / relative
        if not artifact.is_file() or file_hash(artifact) != expected:
            raise ValueError(f"Artefact qualité absent ou modifié : {relative}")
    contract = report.get("downstream_contract", {})
    admissible_path = Path(contract.get("input", ""))
    if not admissible_path.is_file() or admissible_path.parent != root:
        raise ValueError("Flux admissible absent ou hors du rapport qualité")
    allowed = set(contract.get("allowed_statuses", []))
    records = [
        json.loads(line)
        for line in admissible_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(
        record.get("status") not in allowed
        or record.get("downstream_eligible") is not True
        for record in records
    ):
        raise ValueError("Le flux admissible contient une valeur en quarantaine")
    return records


def main(argv: list[str] | None = None) -> None:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    if any(arg == "--candidates" or arg.startswith("--candidates=") for arg in argv):
        # Mode « handoff » : contrôle des candidats JSON de l'Extraction
        # (voir docs/fqg_ammc_integration_contract.md).
        from moroccan_markets_ai.data.parsers.fqg_pipeline import main as handoff_main

        handoff_main(argv)
        return

    cli = argparse.ArgumentParser(description="Financial Data Quality Gate")
    cli.add_argument("integrated_document", type=Path)
    cli.add_argument("--rules", type=Path)
    cli.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = cli.parse_args(argv)
    result = evaluate_quality(
        args.integrated_document,
        rules_path=args.rules,
        output_root=args.output_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
