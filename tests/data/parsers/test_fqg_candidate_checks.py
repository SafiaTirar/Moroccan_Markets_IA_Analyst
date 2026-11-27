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


def test_different_values_in_two_reports_are_other_versions_not_a_conflict(tmp_path):
    """Même observation (2024) publiée avec deux valeurs dans deux rapports."""
    published = cand(
        annee=2024,
        report_year=2024,
        valeur=726492948.0,
        ligne_source="TOTAL ACTIF | 800 000 000 | 726 492 948",
    )
    restated = cand(
        annee=2024,
        report_year=2025,
        valeur=800000000.0,
        source_table="DOCLING_090",
        ligne_source="TOTAL ACTIF | 795 461 302 | 800 000 000",
    )
    decisions, summary = check(tmp_path, [published, restated])
    assert summary.conflict_groups == 0
    assert all("VALUE_CONFLICT" not in codes(d) for d in decisions)
    assert all(d.conflict_group is None for d in decisions)
    assert [d.c.valeur for d in decisions] == [726492948.0, 800000000.0]
    assert len(decisions) == 2  # rien n'est supprimé


def test_same_value_in_two_reports_is_only_corroboration(tmp_path):
    first = cand(annee=2024, report_year=2024)
    other_report = cand(annee=2024, report_year=2025, source_table="DOCLING_090")
    decisions, summary = check(tmp_path, [first, other_report])
    assert summary.conflict_groups == 0
    assert all("CORROBORATED_ELSEWHERE" in codes(d) for d in decisions)


def test_conflict_in_same_report_stays_flagged_when_other_report_differs(tmp_path):
    a = cand(annee=2024, report_year=2025)
    b = cand(
        annee=2024,
        report_year=2025,
        valeur=800000000.0,
        source_table="DOCLING_090",
        ligne_source="TOTAL ACTIF | 800 000 000",
    )
    c = cand(
        annee=2024,
        report_year=2024,
        valeur=900000000.0,
        source_table="DOCLING_091",
        ligne_source="TOTAL ACTIF | 900 000 000",
    )
    decisions, summary = check(tmp_path, [a, b, c])
    assert summary.conflict_groups == 1
    assert "VALUE_CONFLICT" in codes(decisions[0])
    assert "VALUE_CONFLICT" in codes(decisions[1])
    assert "VALUE_CONFLICT" not in codes(decisions[2])


def test_social_and_consolidated_are_never_merged(tmp_path):
    social = cand(perimetre="SOCIAL")
    consolidated = cand(
        perimetre="CONSOLIDE",
        valeur=800000000.0,
        source_table="DOCLING_090",
        ligne_source="TOTAL ACTIF | 800 000 000",
    )
    decisions, summary = check(tmp_path, [social, consolidated])
    assert summary.conflict_groups == 0
    assert all("VALUE_CONFLICT" not in codes(d) for d in decisions)


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


def test_note_references_are_not_amounts(tmp_path):
    record = cand(
        indicator="CUSTOMER_LOANS",
        nom="Prêts et créances sur la clientèle",
        valeur=447885567.0,
        ligne_source="Prêts et créances  | 2.4 / 2.17  | 447 885 567  | 413 590 717",
    )
    (d,), _ = check(tmp_path, [record])
    assert "MULTIPLE_AMOUNTS_IN_CELL" not in codes(d)
    assert "VALUE_NOT_IN_SOURCE_LINE" not in codes(d)


def test_small_integer_amounts_are_kept(tmp_path):
    record = cand(
        entreprise="lesieur_cristal",
        indicator="NET_INCOME_GROUP_SHARE",
        nom="Résultat net part du groupe",
        valeur=24.0,
        ligne_source="Résultat net part du groupe  | 24  | 2",
    )
    (d,), _ = check(tmp_path, [record])
    assert "VALUE_NOT_IN_SOURCE_LINE" not in codes(d)


STROC_LINES = {
    "REVENUE": "- Le Chiffre d'Affaires croit, de 87MMAD à 117MMAD, "
    "la production passe de 88MMAD à 112MMAD sur la même période.",
    "OPERATING_INCOME": "- Le Résultat d'Exploitation de -14MMAD 2024 contre "
    "-22MMAD en 2023, impacté à hauteur de 6MMAD par les dotations.",
    "NET_INCOME": "- Le Résultat Net s'élève ainsi à - 12,5MMAD contre "
    "-39,7MMAD sur l'exercice précédent.",
}


def narrative(indicator, valeur, annee=2024):
    return cand(
        entreprise="stroc",
        report_year=2024,
        annee=annee,
        indicator=indicator,
        nom=indicator,
        valeur=valeur,
        unite="MMAD",
        source_table="NARRATIVE_P005",
        source_page=5,
        source_ligne=85,
        ligne_source=STROC_LINES[indicator],
    )


@pytest.mark.parametrize(
    ("indicator", "valeur", "annee"),
    [
        ("REVENUE", "87", 2023),  # positif accolé : 87MMAD
        ("REVENUE", "117", 2024),
        ("OPERATING_INCOME", "-14", 2024),  # négatif accolé : -14MMAD
        ("OPERATING_INCOME", "-22", 2023),
        ("NET_INCOME", "-12.5", 2024),  # décimal à virgule : - 12,5MMAD
        ("NET_INCOME", "-39.7", 2023),  # décimal à virgule : -39,7MMAD
    ],
)
def test_narrative_amounts_glued_to_unit_are_found(tmp_path, indicator, valeur, annee):
    record = narrative(indicator, valeur, annee)
    (d,), _ = check(tmp_path, [record])
    assert "VALUE_NOT_IN_SOURCE_LINE" not in codes(d)
    assert "MULTIPLE_AMOUNTS_IN_CELL" not in codes(d)
    assert d.c.ligne_source == STROC_LINES[indicator]  # texte source intact


def test_narrative_wrong_value_or_sign_is_still_flagged(tmp_path):
    wrong_sign = narrative("NET_INCOME", "12.5")  # le texte dit - 12,5MMAD
    wrong_value = narrative("REVENUE", "870")
    decisions, _ = check(tmp_path, [wrong_sign, wrong_value])
    assert all("VALUE_NOT_IN_SOURCE_LINE" in codes(d) for d in decisions)
