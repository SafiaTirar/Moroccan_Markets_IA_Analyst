"""Contrôles financiers et déduplication des candidats AMMC (FQG, jour 3).

Les contrôles ne modifient jamais une valeur et ne suppriment aucun candidat :
ils ajoutent des raisons et peuvent seulement rétrograder un statut
(NOT_CHECKED -> REVIEW_REQUIRED -> REJECTED). Ils ne produisent jamais
VERIFIED ni PDF_CONFIRMED.

Voir ``docs/fqg_ammc_integration_contract.md``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    DEFAULT_REGISTRY,
    HANDOFF_SCHEMA_VERSION,
    LoadedCandidate,
    read_json,
)

# Sévérités : INFO (n'affecte pas le statut), REVIEW (-> REVIEW_REQUIRED),
# ERROR (-> REJECTED).
INFO, REVIEW, ERROR = "INFO", "REVIEW", "ERROR"

EXTREME_ABS_VALUE = Decimal("1e13")  # > 10 000 milliards : hors d'échelle AMMC
SMALL_TOTAL_VALUE = Decimal(100)  # un total de bilan < 100 est suspect
NON_NEGATIVE_INDICATORS = frozenset(
    {
        "TOTAL_ASSETS",
        "TOTAL_LIABILITIES_AND_EQUITY",
        "REVENUE",
        "BANKING_NET_REVENUE",
        "INSURANCE_REVENUE",
        "WRITTEN_PREMIUMS",
        "RENTAL_INCOME",
        "CUSTOMER_LOANS",
        "CUSTOMER_DEPOSITS",
    }
)
BALANCE_TOTAL_INDICATORS = frozenset({"TOTAL_ASSETS", "TOTAL_LIABILITIES_AND_EQUITY"})
# Renvoi aux notes annexes (« 2.4 », « 2.4 / 2.17 ») : ce ne sont pas des montants.
# Un entier seul (« 2 », « 24 ») reste un montant possible (ex. en millions).
NOTE_REF_RE = re.compile(
    r"^\d{1,2}\.\d{1,2}(?:\s*/\s*\d{1,2}(?:\.\d{1,2})?)*$"
    r"|^\d{1,2}(?:\.\d{1,2})?(?:\s*/\s*\d{1,2}(?:\.\d{1,2})?)+$"
)
AMOUNT_RE = re.compile(
    r"\(?-?\d{1,3}(?:[ .\u00a0\u202f]\d{3})+(?:,\d+)?\)?|-?\d+(?:,\d+)?"
)


@dataclass
class CandidateDecision:
    loaded: LoadedCandidate
    status: str = "NOT_CHECKED"
    reasons: list[dict[str, str]] = field(default_factory=list)
    duplicate_group: str | None = None
    conflict_group: str | None = None
    linked_anomalies: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None

    def add(self, code: str, severity: str, message: str) -> None:
        self.reasons.append({"code": code, "severity": severity, "message": message})
        if severity == ERROR:
            self.status = "REJECTED"
        elif severity == REVIEW and self.status == "NOT_CHECKED":
            self.status = "REVIEW_REQUIRED"

    @property
    def c(self):  # raccourci vers le candidat Pydantic
        return self.loaded.candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.loaded.candidate_id,
            "input_index": self.loaded.input_index,
            "original": self.loaded.original,
            "normalized": self.loaded.normalized(),
            "status": self.status,
            "reasons": self.reasons,
            "duplicate_group": self.duplicate_group,
            "conflict_group": self.conflict_group,
            "linked_anomalies": self.linked_anomalies,
            "evidence": self.evidence,
            "schema_version": HANDOFF_SCHEMA_VERSION,
        }


def _fold(text: str) -> str:
    """Majuscules sans accents ni apostrophes typographiques."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().upper()


def _source_amounts(line: str) -> list[list[Decimal]]:
    """Montants trouvés dans chaque cellule de ``ligne_source`` (hors libellé)."""
    cells = [cell.strip() for cell in line.split("|")[1:]]
    result = []
    for cell in cells:
        if NOTE_REF_RE.match(cell):
            continue
        amounts = []
        for token in AMOUNT_RE.findall(cell):
            negative = token.startswith(("(", "-"))
            digits = token.strip("()-")
            digits = re.sub(r"[ .\u00a0\u202f]", "", digits).replace(",", ".")
            try:
                value = Decimal(digits)
            except ArithmeticError:
                continue
            amounts.append(-value if negative else value)
        result.append(amounts)
    return result


def _same_amount(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= Decimal("0.005")


# ---------------------------------------------------------------- contrôles unitaires


def check_unit_and_scope(d: CandidateDecision) -> None:
    if d.c.unite is None:
        d.add(
            "MISSING_UNIT", INFO, "unité absente : non inventée, à prouver sur le PDF"
        )
    if d.c.perimetre is None:
        d.add("MISSING_SCOPE", INFO, "périmètre absent : social/consolidé à prouver")


def check_years(d: CandidateDecision) -> None:
    lag = d.c.report_year - d.c.annee
    if lag == 0:
        d.add("CURRENT_YEAR_VALUE", INFO, "valeur de l'exercice du rapport")
    elif lag == 1:
        d.add(
            "COMPARATIVE_VALUE",
            INFO,
            f"valeur comparative {d.c.annee} publiée dans le rapport {d.c.report_year}",
        )
    else:
        d.add(
            "DISTANT_OBSERVATION_YEAR",
            REVIEW,
            f"année {d.c.annee} à {lag} ans du rapport {d.c.report_year} : colonne à vérifier",
        )


def check_value_range(d: CandidateDecision) -> None:
    value = d.c.valeur
    if abs(value) > EXTREME_ABS_VALUE:
        d.add("EXTREME_VALUE", REVIEW, f"valeur hors d'échelle : {value}")
    if value < 0 and d.c.indicator in NON_NEGATIVE_INDICATORS:
        d.add("UNEXPECTED_NEGATIVE", REVIEW, f"{d.c.indicator} négatif : {value}")
    if d.c.indicator in BALANCE_TOTAL_INDICATORS and abs(value) < SMALL_TOTAL_VALUE:
        d.add(
            "SUSPICIOUSLY_SMALL_TOTAL", REVIEW, f"total de bilan très faible : {value}"
        )


def check_source_line(d: CandidateDecision) -> None:
    """La valeur doit être retrouvable dans la ligne source (traçabilité)."""
    cells = _source_amounts(d.c.ligne_source)
    if any(len(cell) > 1 for cell in cells):
        d.add(
            "MULTIPLE_AMOUNTS_IN_CELL",
            REVIEW,
            "une cellule de la ligne source contient plusieurs montants",
        )
    found = any(_same_amount(a, d.c.valeur) for cell in cells for a in cell)
    if not found:
        d.add(
            "VALUE_NOT_IN_SOURCE_LINE",
            REVIEW,
            f"valeur {d.c.valeur} introuvable dans la ligne source",
        )
    # 85.712 lu comme décimal alors que la source écrit 85.712 (milliers) ?
    text = str(d.c.valeur)
    if "." in text and len(text.split(".")[1].rstrip("0")) == 3:
        raw = text.rstrip("0")
        if raw in d.c.ligne_source and "," not in d.c.ligne_source:
            d.add(
                "THOUSANDS_SEPARATOR_MISREAD",
                REVIEW,
                f"'{raw}' probablement un séparateur de milliers (≈ {raw.replace('.', '')})",
            )


def check_label(d: CandidateDecision, registry: dict[str, Any]) -> None:
    label = _fold(d.c.nom)
    ind = d.c.indicator
    sections = re.findall(r"\b[XVI]+ (?:TOTAL|RESULTAT)", label)
    if len(sections) > 1 or (
        "TOTAL PASSIF" in label and "PASSIFS DES ACTIVITES" in label
    ):
        d.add("MERGED_LABEL", REVIEW, f"libellé fusionné : {d.c.nom!r}")
    if ind == "EQUITY" and "PASSIF" in label:
        d.add(
            "LABEL_INDICATOR_MISMATCH",
            REVIEW,
            "libellé « … et passif » = total de bilan, pas des capitaux propres",
        )
    if ind == "EQUITY" and (
        "PART DU GROUPE" in label or "PART GROUPE" in label or "SOCIETE MERE" in label
    ):
        d.add(
            "EQUITY_GROUP_SHARE",
            REVIEW,
            "capitaux propres part du groupe classés en EQUITY (total) : à distinguer",
        )
    if ind == "NET_INCOME" and ("PART DU GROUPE" in label or "PART GROUPE" in label):
        d.add(
            "NET_INCOME_GROUP_SHARE_CONFUSION",
            REVIEW,
            "résultat net part du groupe classé en NET_INCOME",
        )
    if ind == "NET_INCOME_GROUP_SHARE" and "GROUPE" not in label:
        d.add(
            "NET_INCOME_GROUP_SHARE_CONFUSION",
            REVIEW,
            "NET_INCOME_GROUP_SHARE sans mention « part du groupe »",
        )
    entry = registry.get(ind, {})
    for term in entry.get("excluded_label_terms") or []:
        if _fold(term) in label:
            d.add("EXCLUDED_LABEL_TERM", REVIEW, f"terme exclu pour {ind} : {term!r}")
    profile = d.loaded.profile
    if profile and profile in (entry.get("forbidden_profiles") or []):
        d.add(
            "INDICATOR_PROFILE_MISMATCH",
            REVIEW,
            f"{ind} interdit pour le profil {profile}",
        )


# ---------------------------------------------------------------- contrôles de groupe


def _group(decisions, key):
    groups = defaultdict(list)
    for d in decisions:
        groups[key(d)].append(d)
    return groups


def check_exact_duplicates(decisions: list[CandidateDecision]) -> int:
    """Doublon exact = même candidat (tous champs). Le 1er est gardé, les autres rejetés."""
    count = 0
    groups = _group(decisions, lambda d: d.loaded.candidate_id)
    for n, (_, items) in enumerate(sorted(groups.items()), start=1):
        if len(items) < 2:
            continue
        gid = f"dup-{n}"
        items.sort(key=lambda d: d.loaded.input_index)
        for d in items:
            d.duplicate_group = gid
        for d in items[1:]:
            count += 1
            d.add(
                "EXACT_DUPLICATE",
                ERROR,
                f"doublon exact de l'entrée {items[0].loaded.input_index} (conservée)",
            )
    return count


def check_value_conflicts(decisions: list[CandidateDecision]) -> int:
    """Même entreprise, année observée et indicateur, mais valeurs différentes.

    Toutes les sources sont conservées : chaque membre reçoit un conflict_group.
    Une différence d'un facteur 1 000 / 1 000 000 est signalée comme écart d'échelle.
    """
    groups = _group(decisions, lambda d: (d.c.entreprise, d.c.annee, d.c.indicator))
    n_conflicts = 0
    for n, (key, items) in enumerate(sorted(groups.items()), start=1):
        values = {d.c.valeur for d in items}
        if len(values) < 2:
            if len(items) > 1:
                for d in items:
                    d.add(
                        "CORROBORATED_ELSEWHERE",
                        INFO,
                        f"même valeur trouvée dans {len(items)} sources",
                    )
            continue
        n_conflicts += 1
        gid = f"conflict-{n}"
        if max(values) - min(values) <= Decimal(1):
            for d in items:
                d.conflict_group = f"rounding-{n}"
                d.add(
                    "ROUNDING_DIFFERENCE",
                    INFO,
                    f"écart d'arrondi ≤ 1 entre sources : {sorted(map(str, values))}",
                )
            continue
        nonzero = sorted(abs(v) for v in values if v)
        scale_only = bool(nonzero) and _all_scale_multiples(nonzero)
        same_report = len({d.c.report_year for d in items}) == 1
        for d in items:
            d.conflict_group = gid
            others = sorted(str(v) for v in values if v != d.c.valeur)
            if scale_only:
                d.add(
                    "UNIT_SCALE_DIVERGENCE",
                    REVIEW,
                    f"mêmes chiffres à une échelle près ({', '.join(others)}) : unités à confirmer",
                )
            else:
                where = "dans le même rapport" if same_report else "entre rapports"
                d.add(
                    "VALUE_CONFLICT",
                    REVIEW,
                    f"valeurs concurrentes {where} pour {key[0]} {key[1]} {key[2]} : "
                    f"{', '.join(others)} (périmètre/version à départager)",
                )
    return n_conflicts


def _all_scale_multiples(values: list[Decimal]) -> bool:
    """Vrai si les valeurs ne diffèrent que d'un facteur 1 000 ou 1 000 000."""
    base = values[0]
    if values[-1] / base < Decimal(500):
        return False
    for v in values[1:]:
        ratio = v / base
        if not any(
            abs(ratio - factor) / factor < Decimal("0.01")
            for factor in (Decimal(1), Decimal(1000), Decimal(1_000_000))
        ):
            return False
    return True


def check_balance(decisions: list[CandidateDecision]) -> int:
    """Total actif vs total passif : même entreprise, même rapport, même année observée."""
    groups = _group(decisions, lambda d: (d.c.entreprise, d.c.report_year, d.c.annee))
    mismatches = 0
    for items in groups.values():
        assets = [d for d in items if d.c.indicator == "TOTAL_ASSETS"]
        liabilities = [
            d for d in items if d.c.indicator == "TOTAL_LIABILITIES_AND_EQUITY"
        ]
        if not assets or not liabilities:
            continue
        for a in assets:
            for li in liabilities:
                if _same_amount(a.c.valeur, li.c.valeur):
                    for d in (a, li):
                        d.add(
                            "BALANCE_OK",
                            INFO,
                            "total actif = total passif (indice, pas une preuve)",
                        )
                else:
                    mismatches += 1
                    msg = f"total actif {a.c.valeur} ≠ total passif {li.c.valeur}"
                    a.add("ASSETS_LIABILITIES_MISMATCH", REVIEW, msg)
                    li.add("ASSETS_LIABILITIES_MISMATCH", REVIEW, msg)
    return mismatches


def check_net_income_vs_group_share(decisions: list[CandidateDecision]) -> int:
    """Signale NET_INCOME et NET_INCOME_GROUP_SHARE identiques (confusion possible)."""
    groups = _group(decisions, lambda d: (d.c.entreprise, d.c.report_year, d.c.annee))
    count = 0
    for items in groups.values():
        totals = [d for d in items if d.c.indicator == "NET_INCOME"]
        shares = [d for d in items if d.c.indicator == "NET_INCOME_GROUP_SHARE"]
        for t in totals:
            for s in shares:
                if _same_amount(t.c.valeur, s.c.valeur):
                    count += 1
                    msg = "résultat net total = part du groupe : confusion ou absence de minoritaires"
                    t.add("NET_INCOME_EQUALS_GROUP_SHARE", INFO, msg)
                    s.add("NET_INCOME_EQUALS_GROUP_SHARE", INFO, msg)
    return count


def link_anomalies(
    decisions: list[CandidateDecision], anomalies: list[dict[str, Any]]
) -> None:
    by_cell = defaultdict(list)
    by_table = defaultdict(list)
    for d in decisions:
        c = d.c
        by_cell[
            (c.entreprise, c.report_year, c.source_table, c.source_ligne, c.indicator)
        ].append(d)
        by_table[(c.entreprise, c.report_year, c.source_table)].append(d)
    for anomaly in anomalies:
        reason = anomaly.get("raison")
        key3 = (
            anomaly.get("entreprise"),
            anomaly.get("report_year"),
            anomaly.get("table_id"),
        )
        if reason == "MONTANT_A_VERIFIER":
            key = (*key3, anomaly.get("ligne"), anomaly.get("indicator"))
            for d in by_cell.get(key, []):
                d.linked_anomalies.append(anomaly)
                d.add(
                    "LINKED_AMOUNT_ANOMALY",
                    REVIEW,
                    "anomalie MONTANT_A_VERIFIER sur cette ligne",
                )
        elif reason == "TABLEAU_TO_REVIEW":
            for d in by_table.get(key3, []):
                d.linked_anomalies.append(anomaly)
                d.add(
                    "LINKED_TABLE_ANOMALY",
                    REVIEW,
                    "tableau source signalé TABLEAU_TO_REVIEW",
                )


# ---------------------------------------------------------------- orchestration


@dataclass
class CheckSummary:
    exact_duplicates: int
    conflict_groups: int
    balance_mismatches: int
    net_income_equal_group_share: int


def load_registry_entries(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    return {item["indicator"]: item for item in read_json(path)["indicators"]}


def run_checks(
    loaded: list[LoadedCandidate],
    anomalies: list[dict[str, Any]] | None = None,
    registry: dict[str, Any] | None = None,
) -> tuple[list[CandidateDecision], CheckSummary]:
    registry = registry if registry is not None else load_registry_entries()
    decisions = [CandidateDecision(item) for item in loaded]
    for d in decisions:
        check_unit_and_scope(d)
        check_years(d)
        check_value_range(d)
        check_source_line(d)
        check_label(d, registry)
    summary = CheckSummary(
        exact_duplicates=check_exact_duplicates(decisions),
        conflict_groups=check_value_conflicts(decisions),
        balance_mismatches=check_balance(decisions),
        net_income_equal_group_share=check_net_income_vs_group_share(decisions),
    )
    link_anomalies(decisions, anomalies or [])
    assert len(decisions) == len(loaded), "perte silencieuse dans les contrôles"
    return decisions, summary
