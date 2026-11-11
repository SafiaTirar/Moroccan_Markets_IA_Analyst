import json
import re
from pathlib import Path

from moroccan_markets_ai.extraction.csv_loader import charger_tableaux
from moroccan_markets_ai.extraction.indicator_value_extractor import (
    extraire_valeurs_indicateurs,
)
from moroccan_markets_ai.extraction.table_config import obtenir_configuration
from moroccan_markets_ai.extraction.table_detector import detecter_type_tableau
from moroccan_markets_ai.extraction.table_diagnostic import diagnostiquer_tableau

RACINE = (
    Path.home()
    / "Desktop"
    / "AMMC_annual_45"
    / "artifacts"
    / "handover"
    / "AMMC_annual_45"
)

SORTIE = Path("data/quality/ammc/extraction_globale")

# Ajouter ici uniquement les identifiants de tableaux Sonasid 2023
# dont le format numérique a été confirmé dans le PDF original.
#
# Exemple, après vérification :
# TABLEAUX_POINTS_MILLIERS = {
#     ("sonasid", 2023, "DOCLING_XXX"),
# }
TABLEAUX_POINTS_MILLIERS = {
    ("sonasid", 2023, "DOCLING_043"),
}

# Accepter uniquement les montants isolés.
MONTANT = re.compile(
    r"^[+-]?\s*(?:"
    r"\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?"
    r"|"
    r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:,\d+)?"
    r"|"
    r"\d+(?:[,.]\d+)?"
    r")$"
)


def enregistrer(chemin, donnees):
    """Enregistre les résultats au format JSON."""

    chemin.parent.mkdir(parents=True, exist_ok=True)

    chemin.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def traiter_rapport(entreprise, annee):
    """Extrait les candidats financiers d'un rapport AMMC."""

    dossier = RACINE / entreprise / str(annee)

    candidats = []
    anomalies = []

    tableaux_financiers = 0
    tableaux_ready = 0

    if not dossier.exists():
        return [], [{
            "raison": "DOSSIER_ABSENT",
            "detail": str(dossier),
        }], 0, 0

    try:
        tableaux = charger_tableaux(dossier)

    except Exception as erreur:
        return [], [{
            "raison": "CHARGEMENT_IMPOSSIBLE",
            "detail": str(erreur),
        }], 0, 0

    for tableau in tableaux:
        tableau["entreprise"] = entreprise
        tableau["annee"] = annee

        table_id = tableau["table_id"]

        # Utiliser le point comme séparateur de milliers uniquement
        # pour un tableau explicitement confirmé.
        tableau["point_milliers"] = (
            entreprise,
            annee,
            table_id,
        ) in TABLEAUX_POINTS_MILLIERS

        try:
            # 1. Identifier le type de tableau financier.
            type_tableau = detecter_type_tableau(tableau)

            if type_tableau is None:
                continue

            tableaux_financiers += 1

            # 2. Vérifier l'exploitabilité des colonnes.
            diagnostic = diagnostiquer_tableau(tableau)

            if diagnostic["statut"] != "READY":
                anomalies.append({
                    "table_id": table_id,
                    "pages": tableau.get("pages", []),
                    "raison": "TABLEAU_TO_REVIEW",
                    "detail": diagnostic.get("raison"),
                })
                continue

            tableaux_ready += 1

            # 3. Configurer les colonnes et extraire les valeurs.
            config = obtenir_configuration(tableau)
            resultats = extraire_valeurs_indicateurs(tableau)
            lignes = tableau["contenu"].splitlines()

            # 4. Parcourir les indicateurs extraits.
            for resultat in resultats:
                annee_observation = resultat["observation_year"]

                indice_colonne = config["colonnes"].get(
                    annee_observation
                )

                numero_ligne = resultat["ligne"]

                # Vérifier les positions.
                if (
                    indice_colonne is None
                    or numero_ligne < 0
                    or numero_ligne >= len(lignes)
                ):
                    anomalies.append({
                        "table_id": table_id,
                        "indicator": resultat["indicator"],
                        "observation_year": annee_observation,
                        "raison": "POSITION_INVALIDE",
                    })
                    continue

                cellules = lignes[numero_ligne].split("|")

                cellule = (
                    cellules[indice_colonne].strip()
                    if indice_colonne < len(cellules)
                    else ""
                )

                # 5. Rejeter les cellules ambiguës.
                if (
                    resultat["status"] != "CANDIDATE"
                    or resultat["value"] is None
                    or not MONTANT.fullmatch(cellule)
                ):
                    anomalies.append({
                        "table_id": table_id,
                        "ligne": numero_ligne,
                        "indicator": resultat["indicator"],
                        "observation_year": annee_observation,
                        "cellule_source": cellule,
                        "raison": "MONTANT_A_VERIFIER",
                    })
                    continue

                # 6. Conserver l'observation candidate.
                candidats.append({
                    "entreprise": entreprise,
                    "report_year": annee,
                    "observation_year": annee_observation,
                    "indicator": resultat["indicator"],
                    "value": resultat["value"],
                    "value_source": cellule,
                    "unite": None,
                    "perimetre": None,
                    "type_tableau": type_tableau,
                    "source_table": table_id,
                    "source_pages": tableau.get("pages", []),
                    "source_ligne": numero_ligne,
                    "ligne_source": resultat["texte_source"],
                    "validation_status": "REVIEW_REQUIRED",
                })

        except Exception as erreur:
            anomalies.append({
                "table_id": table_id,
                "pages": tableau.get("pages", []),
                "raison": "ERREUR_EXTRACTION",
                "detail": str(erreur),
            })

    # Signaler les rapports sans observation extraite.
    if not candidats:
        anomalies.append({
            "raison": "AUCUN_CANDIDAT_EXTRAIT",
            "detail": (
                "Aucune observation exploitable produite "
                "par l'extraction structurée."
            ),
        })

    return (
        candidats,
        anomalies,
        tableaux_financiers,
        tableaux_ready,
    )


def main():
    """Exécute l'extraction sur les 45 rapports AMMC."""

    configuration = json.loads(
        Path("config/data/ammc_corpus.json").read_text(
            encoding="utf-8"
        )
    )

    tous_candidats = []
    toutes_anomalies = []
    bilan = []

    for compagnie in configuration["companies"]:
        entreprise = compagnie["company_id"]

        for annee in configuration["target_years"]:
            candidats, anomalies, financiers, ready = traiter_rapport(
                entreprise,
                annee,
            )

            tous_candidats.extend(candidats)

            # Conserver l'entreprise et l'année pour les anomalies.
            for anomalie in anomalies:
                toutes_anomalies.append({
                    "entreprise": entreprise,
                    "report_year": annee,
                    **anomalie,
                })

            bilan.append({
                "entreprise": entreprise,
                "annee": annee,
                "tableaux_financiers": financiers,
                "tableaux_ready": ready,
                "candidats": len(candidats),
                "anomalies": len(anomalies),
            })

            print(
                entreprise,
                annee,
                "| Candidats :", len(candidats),
                "| Anomalies :", len(anomalies),
            )

    # Export des résultats.
    enregistrer(
        SORTIE / "candidats.json",
        tous_candidats,
    )

    enregistrer(
        SORTIE / "anomalies.json",
        toutes_anomalies,
    )

    enregistrer(
        SORTIE / "bilan.json",
        bilan,
    )

    print("\n=== BILAN GLOBAL ===")
    print("Rapports traités :", len(bilan))
    print("Candidats :", len(tous_candidats))
    print("Anomalies :", len(toutes_anomalies))
    print("Dossier de sortie :", SORTIE)


if __name__ == "__main__":
    main()
