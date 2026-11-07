import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.fqg_candidate_checks import run_checks
from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    DEFAULT_HANDOFF_DIR,
    load_anomalies,
    load_candidates,
)

BASE = {
    "nom": "TOTAL ACTIF",
    "annee": 2025,
    "valeur": 795461302.0,
    "ligne_source": "TOTAL ACTIF  |  | 795 461 302  | 726 492 948",
    "entreprise": "attijariwafa_bank",
    "unite": None,
    "perimetre": None,
    "source_table": "DOCLING_083",
    "source_page": 95,
    "source_ligne": 25,
    "validation_status": "NOT_CHECKED",
    "indicator": "TOTAL_ASSETS",
    "report_year": 2025,
}


def cand(**changes):
    record = dict(BASE)
    record.update(changes)
    return record


def check(tmp_path: Path, records, anomalies=None):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    result = load_candidates(path)
    assert not result.rejected
    return run_checks(result.loaded, anomalies or [])


def codes(decision):
    return {r["code"] for r in decision.reasons}


def test_clean_candidate_stays_not_checked_and_flags_missing_context(tmp_path):
    (d,), _ = check(tmp_path, [cand()])
    assert d.status == "NOT_CHECKED"
    assert {"MISSING_UNIT", "MISSING_SCOPE", "CURRENT_YEAR_VALUE"} <= codes(d)


def test_checks_never_promote_to_verified(tmp_path):
    decisions, _ = check(tmp_path, [cand(), cand(annee=2024, valeur=726492948.0)])
    assert all(d.status not in {"VERIFIED", "PDF_CONFIRMED"} for d in decisions)


def test_original_value_is_never_modified(tmp_path):
    record = cand(valeur=85.712, ligne_source="RÉSULTAT NET | 85.712 | 105.721")
    (d,), _ = check(tmp_path, [record])
    assert d.to_dict()["original"]["valeur"] == 85.712


def test_exact_duplicate_keeps_first_and_rejects_others(tmp_path):
    decisions, summary = check(tmp_path, [cand(), cand(), cand()])
    assert summary.exact_duplicates == 2
    assert [d.status for d in decisions] == ["NOT_CHECKED", "REJECTED", "REJECTED"]
    assert len({d.duplicate_group for d in decisions}) == 1
    assert len(decisions) == 3  # rien n'est supprimé


def test_value_conflict_keeps_all_sources(tmp_path):
    other = cand(
        valeur=800000000.0,
        source_table="DOCLING_090",
        ligne_source="TOTAL ACTIF | 800 000 000",
    )
    decisions, summary = check(tmp_path, [cand(), other])
    assert summary.conflict_groups == 1
    assert all(d.status == "REVIEW_REQUIRED" for d in decisions)
    assert all("VALUE_CONFLICT" in codes(d) for d in decisions)
    assert decisions[0].conflict_group == decisions[1].conflict_group


def test_scale_divergence_is_named_as_such(tmp_path):
    other = cand(
        valeur=795461.302,
        source_table="DOCLING_090",
        ligne_source="TOTAL ACTIF | 795 461,302",
    )
    decisions, _ = check(tmp_path, [cand(), other])
    assert all("UNIT_SCALE_DIVERGENCE" in codes(d) for d in decisions)


def test_rounding_difference_is_info_only(tmp_path):
    a = cand(
        indicator="EQUITY",
        nom="Capitaux propres",
        valeur=1458514079.08,
        ligne_source="Capitaux propres | 1 458 514 079,08",
    )
    b = cand(
        indicator="EQUITY",
        nom="Capitaux propres",
        valeur=1458514079.09,
        source_table="DOCLING_090",
        ligne_source="Capitaux propres | 1 458 514 079,09",
    )
    decisions, _ = check(tmp_path, [a, b])
    assert all("ROUNDING_DIFFERENCE" in codes(d) for d in decisions)
    assert all(d.status == "NOT_CHECKED" for d in decisions)


def test_report_year_vs_observation_year(tmp_path):
    decisions, _ = check(
        tmp_path,
        [
            cand(),
            cand(annee=2024, valeur=726492948.0),
            cand(annee=2023, valeur=726492948.0, source_table="DOCLING_091"),
        ],
    )
    assert "CURRENT_YEAR_VALUE" in codes(decisions[0])
    assert "COMPARATIVE_VALUE" in codes(decisions[1])
    assert "DISTANT_OBSERVATION_YEAR" in codes(decisions[2])
    assert decisions[2].status == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {"valeur": 2e13, "ligne_source": "TOTAL ACTIF | 20000000000000"},
            "EXTREME_VALUE",
        ),
        ({"valeur": -5.0, "ligne_source": "TOTAL ACTIF | -5"}, "UNEXPECTED_NEGATIVE"),
        (
            {"valeur": 12.0, "ligne_source": "TOTAL ACTIF | 12"},
            "SUSPICIOUSLY_SMALL_TOTAL",
        ),
        ({"valeur": 123.0}, "VALUE_NOT_IN_SOURCE_LINE"),
        (
            {"ligne_source": "TOTAL ACTIF | 795 461 302,00 726 492 948,00"},
            "MULTIPLE_AMOUNTS_IN_CELL",
        ),
    ],
)
def test_suspicious_values(tmp_path, changes, code):
    (d,), _ = check(tmp_path, [cand(**changes)])
    assert code in codes(d)
    assert d.status == "REVIEW_REQUIRED"


def test_negative_net_income_is_allowed(tmp_path):
    record = cand(
        indicator="NET_INCOME",
        nom="Résultat net",
        valeur=-5000000.0,
        ligne_source="Résultat net | -5 000 000",
    )
    (d,), _ = check(tmp_path, [record])
    assert "UNEXPECTED_NEGATIVE" not in codes(d)


def test_thousands_separator_misread(tmp_path):
    record = cand(
        entreprise="sonasid",
        indicator="NET_INCOME_GROUP_SHARE",
        nom="RÉSULTAT NET - PART DU GROUPE",
        valeur=85.712,
        ligne_source="RÉSULTAT NET - PART DU GROUPE  | 85.712  | 105.721",
    )
    (d,), _ = check(tmp_path, [record])
    assert "THOUSANDS_SEPARATOR_MISREAD" in codes(d)


def test_assets_liabilities_mismatch(tmp_path):
    passif = cand(
        indicator="TOTAL_LIABILITIES_AND_EQUITY",
        nom="TOTAL PASSIF",
        valeur=700000000.0,
        source_table="DOCLING_084",
        ligne_source="TOTAL PASSIF | 700 000 000",
    )
    decisions, summary = check(tmp_path, [cand(), passif])
    assert summary.balance_mismatches == 1
    assert all("ASSETS_LIABILITIES_MISMATCH" in codes(d) for d in decisions)


def test_balance_ok_is_only_an_info(tmp_path):
    passif = cand(
        indicator="TOTAL_LIABILITIES_AND_EQUITY",
        nom="TOTAL PASSIF",
        source_table="DOCLING_084",
        ligne_source="TOTAL PASSIF | 795 461 302",
    )
    decisions, _ = check(tmp_path, [cand(), passif])
    assert all("BALANCE_OK" in codes(d) for d in decisions)
    assert all(d.status == "NOT_CHECKED" for d in decisions)


@pytest.mark.parametrize(
    ("indicator", "label", "code"),
    [
        ("EQUITY", "TOTAL CAPITAUX PROPRES ET PASSIF", "LABEL_INDICATOR_MISMATCH"),
        ("EQUITY", "Capitaux propres part du groupe", "EQUITY_GROUP_SHARE"),
        (
            "NET_INCOME",
            "Résultat net part du groupe",
            "NET_INCOME_GROUP_SHARE_CONFUSION",
        ),
        (
            "NET_INCOME_GROUP_SHARE",
            "Résultat net de l'exercice",
            "NET_INCOME_GROUP_SHARE_CONFUSION",
        ),
        ("NET_INCOME", "Résultat net par action", "EXCLUDED_LABEL_TERM"),
        ("REVENUE", "Chiffre d'affaires", "INDICATOR_PROFILE_MISMATCH"),  # banque
        (
            "NET_INCOME",
            "XIV TOTAL DES PRODUITS (I + IV) XV TOTAL DES CHARGES XVI RESULTAT NET",
            "MERGED_LABEL",
        ),
    ],
)
def test_label_checks(tmp_path, indicator, label, code):
    (d,), _ = check(tmp_path, [cand(indicator=indicator, nom=label)])
    assert code in codes(d)


def test_single_roman_section_label_is_not_merged(tmp_path):
    label = "XVI RESULTAT NET (Total des produits -Total des charges)"
    (d,), _ = check(tmp_path, [cand(indicator="NET_INCOME", nom=label)])
    assert "MERGED_LABEL" not in codes(d)


def test_net_income_equal_group_share_is_flagged(tmp_path):
    total = cand(
        indicator="NET_INCOME",
        nom="Résultat net",
        ligne_source="Résultat net | 795 461 302",
    )
    share = cand(
        indicator="NET_INCOME_GROUP_SHARE",
        nom="Résultat net part du groupe",
        source_table="DOCLING_084",
        ligne_source="Résultat net part du groupe | 795 461 302",
    )
    decisions, summary = check(tmp_path, [total, share])
    assert summary.net_income_equal_group_share == 1
    assert all("NET_INCOME_EQUALS_GROUP_SHARE" in codes(d) for d in decisions)


def test_anomalies_are_linked(tmp_path):
    anomalies = [
        {
            "entreprise": "attijariwafa_bank",
            "report_year": 2025,
            "table_id": "DOCLING_083",
            "ligne": 25,
            "indicator": "TOTAL_ASSETS",
            "observation_year": 2025,
            "cellule_source": "",
            "raison": "MONTANT_A_VERIFIER",
        },
        {
            "entreprise": "attijariwafa_bank",
            "report_year": 2025,
            "table_id": "DOCLING_083",
            "pages": [95],
            "raison": "TABLEAU_TO_REVIEW",
            "detail": "x",
        },
    ]
    (d,), _ = check(tmp_path, [cand()], anomalies)
    assert {"LINKED_AMOUNT_ANOMALY", "LINKED_TABLE_ANOMALY"} <= codes(d)
    assert len(d.linked_anomalies) == 2


@pytest.mark.skipif(
    not (DEFAULT_HANDOFF_DIR / "candidats_fqg.json").exists(),
    reason="handoff_fqg_45 absent",
)
def test_real_handoff_checks():
    result = load_candidates()
    decisions, _summary = run_checks(result.loaded, load_anomalies())
    assert len(decisions) == 329
    statuses = {d.status for d in decisions}
    assert statuses <= {"NOT_CHECKED", "REVIEW_REQUIRED", "REJECTED"}
    sonasid = [
        d
        for d in decisions
        if d.c.entreprise == "sonasid"
        and d.c.valeur < 1000
        and d.c.indicator == "NET_INCOME_GROUP_SHARE"
    ]
    assert sonasid and all("THOUSANDS_SEPARATOR_MISREAD" in codes(d) for d in sonasid)