import json
from decimal import Decimal

import pytest

from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    candidate_id,
)
from moroccan_markets_ai.extraction import narrative_extractor
from moroccan_markets_ai.extraction.handoff_exporter import (
    conserver_valeurs_historiques,
)
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


def test_conservation_decisions_verified(tmp_path):
    """Les décisions VERIFIED doivent rester associées aux mêmes candidats."""

    ancien = {
        "entreprise": "entreprise_test",
        "report_year": 2025,
        "indicator": "REVENUE",
        "annee": 2025,
        "source_table": "DOCLING_001",
        "source_page": 10,
        "source_ligne": 5,
        "valeur": 1500000.0,
        "validation_status": "NOT_CHECKED",
    }

    nouveau = {
        **ancien,
        "valeur": "1500000",
    }

    decision_humaine = {
        "candidate_id": candidate_id(ancien),
        "decision": "VERIFIED",
    }

    historique = tmp_path / "candidats_v1.json"
    historique.write_text(
        json.dumps([ancien]),
        encoding="utf-8",
    )

    corriges = conserver_valeurs_historiques(
        [nouveau],
        historique,
    )

    nouveaux_ids = {candidate_id(c) for c in corriges}

    assert decision_humaine["candidate_id"] in nouveaux_ids
    assert corriges[0]["validation_status"] == "NOT_CHECKED"

def test_sonasid_resultat_global_non_classe_comme_resultat_net(
    monkeypatch,
):
    """Le résultat global ne doit pas être classé comme résultat net."""

    from moroccan_markets_ai.extraction import indicator_matcher

    monkeypatch.setattr(
        indicator_matcher,
        "obtenir_indicateurs",
        lambda entreprise: [
            {
                "indicator": "NET_INCOME_GROUP_SHARE",
                "synonyms": [
                    "résultat global part du groupe",
                    "résultat net part du groupe",
                ],
            }
        ],
    )

    monkeypatch.setattr(
        indicator_matcher,
        "detecter_type_tableau",
        lambda tableau: "COMPTE_RESULTAT",
    )

    tableau = {
        "entreprise": "sonasid",
        "table_id": "DOCLING_043",
        "contenu": (
            "RESULTAT GLOBAL - PART DU GROUPE | 85.712 | 105.721\n"
            "RESULTAT NET - PART DU GROUPE | 80.000 | 90.000"
        ),
    }

    correspondances = indicator_matcher.trouver_indicateurs(tableau)

    assert len(correspondances) == 1
    assert correspondances[0]["indicator"] == "NET_INCOME_GROUP_SHARE"
    assert "RESULTAT NET" in correspondances[0]["texte"]

def test_integration_conservation_decisions_fqg(tmp_path):
    """Vérifie l'export FQG et la conservation des décisions humaines."""
    from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
        candidate_id,
        load_candidates,
    )
    from moroccan_markets_ai.extraction.handoff_exporter import (
        conserver_valeurs_historiques,
    )

    anciens = []
    nouveaux = []
    decisions = []

    for i in range(13):
        valeur = 1000000 + i

        ancien = {
            "entreprise": "attijariwafa_bank",
            "nom": "Indicateur financier",
            "indicator": "REVENUE",
            "annee": 2025,
            "report_year": 2025,
            "valeur": float(valeur),
            "ligne_source": f"Indicateur financier | {valeur}",
            "unite": None,
            "perimetre": None,
            "source_table": f"DOCLING_{i + 1:03d}",
            "source_page": 10,
            "source_ligne": i,
            "validation_status": "NOT_CHECKED",
        }

        anciens.append(ancien)
        nouveaux.append({**ancien, "valeur": str(valeur)})

        decisions.append({
            "candidate_id": candidate_id(ancien),
            "decision": "VERIFIED",
        })

    # 1. Créer un handoff historique temporaire.
    historique = tmp_path / "historique.json"
    historique.write_text(
        json.dumps(anciens, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2. Préserver les identifiants historiques.
    corriges = conserver_valeurs_historiques(
        nouveaux,
        historique,
    )

    # 3. Créer le nouveau fichier de candidats.
    fichier_fqg = tmp_path / "candidats_fqg.json"
    fichier_fqg.write_text(
        json.dumps(corriges, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. Charger les candidats avec le chargeur officiel FQG.
    resultat = load_candidates(fichier_fqg)

    assert resultat.input_count == 13
    assert len(resultat.loaded) == 13
    assert len(resultat.rejected) == 0

    # 5. Vérifier que les décisions historiques restent retrouvables.
    nouveaux_ids = {
        candidate_id(c)
        for c in corriges
    }

    assert all(
        decision["candidate_id"] in nouveaux_ids
        for decision in decisions
    )

    # Aucune validation humaine ne doit être attribuée automatiquement.
    assert all(
        candidat["validation_status"] == "NOT_CHECKED"
        for candidat in corriges
    )
