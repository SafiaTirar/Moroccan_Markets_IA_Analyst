from .indicator_matcher import trouver_indicateurs
from .line_extractor import convertir_montant
from .table_config import obtenir_configuration


def extraire_valeurs_indicateurs(tableau):
    """Extrait les montants candidats des indicateurs détectés."""

    config = obtenir_configuration(tableau)
    correspondances = trouver_indicateurs(tableau)

    resultats = []

    for correspondance in correspondances:
        cellules = correspondance["texte"].split(" | ")

        for annee, indice in config["colonnes"].items():
            montant = None

            if indice < len(cellules):
                montant = convertir_montant(cellules[indice])

            resultats.append({
                "table_id": tableau["table_id"],
                "indicator": correspondance["indicator"],
                "observation_year": annee,
                "value": str(montant) if montant is not None else None,
                "ligne": correspondance["ligne"],
                "texte_source": correspondance["texte"],
                "status": (
                    "CANDIDATE"
                    if montant is not None
                    else "MISSING"
                ),
            })

    return resultats
