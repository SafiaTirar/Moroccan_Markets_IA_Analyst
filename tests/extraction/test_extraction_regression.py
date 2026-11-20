from decimal import Decimal

import pytest

from moroccan_markets_ai.extraction import narrative_extractor
from moroccan_markets_ai.extraction.indicator_matcher import (
    trouver_indicateurs,
)
from moroccan_markets_ai.extraction.indicator_value_extractor import (
    convertir_montant_financier,
)

# 1. SONASID : conversion des montants financiers.

@pytest.mark.parametrize(
    "montant, point_milliers, attendu",
    [
        ("85.712", True, "85712"),
        ("105.721", True, "105721"),
        ("85.712", False, "85.712"),
        ("1234.56", False, "1234.56"),
        ("9.332.718", False, "9332718"),
    ],
)
def test_sonasid_montants(montant, point_milliers, attendu):
    resultat = convertir_montant_financier(
        montant,
        point_milliers=point_milliers,
    )

    assert Decimal(str(resultat)) == Decimal(attendu)


# 2. EQUITY : classification des capitaux propres.

@pytest.mark.parametrize(
    "libelle, attendu",
    [
        ("Capitaux propres", "EQUITY"),
        ("Capitaux propres part du Groupe", None),
        ("Capitaux propres attribuables aux actionnaires ordinaires", None),
        ("Capitaux propres de l'ensemble consolidé", "EQUITY"),
        ("TOTAL CAPITAL PROPRES ET PASSIF".replace(
            "CAPITAL", "CAPITAUX"
        ), "TOTAL_LIABILITIES_AND_EQUITY"),
        ("TOTAL PASSIF", None),
        ("Résultat net part du Groupe", "NET_INCOME_GROUP_SHARE"),
    ],
)
def test_equity_mapping(libelle, attendu):
    tableau = {
        "entreprise": "sonasid",
        "annee": 2024,
        "table_id": "TEST",
        "contenu": libelle,
    }

    resultats = trouver_indicateurs(tableau)

    obtenu = resultats[0]["indicator"] if resultats else None

    assert obtenu == attendu


# 3. TOTALENERGIES : libellés dans la deuxième colonne.

@pytest.mark.parametrize("annee", [2023, 2024, 2025])
def test_totalenergies_designation(annee):
    tableau = {
        "entreprise": "totalenergies_marketing_maroc",
        "annee": annee,
        "table_id": "DOCLING_003",
        "contenu": (
            f" | DESIGNATION | {annee} | {annee - 1}\n"
            "I | Chiffres d'affaires | 1000 | 900\n"
            "XIII | RESULTAT NET | 200 | 150"
        ),
    }

    resultats = trouver_indicateurs(tableau)

    codes = [r["indicator"] for r in resultats]

    assert "REVENUE" in codes
    assert "NET_INCOME" in codes


# 4. STROC : extraction narrative sans lancer Ollama.

def test_stroc_narratif(monkeypatch):
    texte = (
        "Les exercices 2023 et 2024 :\n"
        "- Le Chiffre d'Affaires croit, de 87MMAD à 117MMAD, "
        "la production passe de 88MMAD à 112MMAD.\n"
        "- L'Excédent Brut d'Exploitation passe "
        "de -12MMAD à -7,8MMAD.\n"
        "- Le Résultat d'Exploitation de -14MMAD 2024 "
        "contre -22MMAD en 2023.\n"
        "- Le Résultat Net s'élève ainsi à - 12,5MMAD "
        "contre -39,7MMAD sur l'exercice précédent."
    )

    reponses = {
        "REVENUE": [
            (2023, "87MMAD"),
            (2024, "117MMAD"),
        ],
        "OPERATING_INCOME": [
            (2023, "-22MMAD"),
            (2024, "-14MMAD"),
        ],
        "NET_INCOME": [
            (2023, "-39,7MMAD"),
            (2024, "-12,5MMAD"),
        ],
    }

    def faux_qwen(
        contexte, ligne, indicator, entreprise, annee_rapport
    ):
        return narrative_extractor.AssociationsAnnees(
            observations=[
                narrative_extractor.AssociationAnnee(
                    annee=annee,
                    montant_source=montant,
                )
                for annee, montant in reponses[indicator]
            ]
        )

    monkeypatch.setattr(
        narrative_extractor,
        "interroger_qwen",
        faux_qwen,
    )

    resultat = narrative_extractor.extraire_narratif(
        texte,
        "stroc",
        2024,
    )

    attendus = {
    ("REVENUE", 2023, Decimal(87)),
    ("REVENUE", 2024, Decimal(117)),
    ("OPERATING_INCOME", 2023, Decimal(-22)),
    ("OPERATING_INCOME", 2024, Decimal(-14)),
    ("NET_INCOME", 2023, Decimal("-39.7")),
    ("NET_INCOME", 2024, Decimal("-12.5")),
    }

    obtenus = {
        (c.indicator, c.annee, c.valeur)
        for c in resultat.indicateurs
    }

    assert len(resultat.indicateurs) == 6
    assert obtenus == attendus

    for candidat in resultat.indicateurs:
        assert candidat.unite == "MMAD"
        assert candidat.ligne_source in texte
