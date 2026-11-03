import re

from .indicator_matcher import trouver_indicateurs
from .line_extractor import convertir_montant
from .table_config import obtenir_configuration


def convertir_montant_financier(cellule):
    """Convertit un montant isolé sans accepter de texte parasite."""

    texte = str(cellule).strip()

    # Cas : 9.332.718 ou -1.588.764 ou 1.588.764,25
    format_points = re.fullmatch(
        r"([+-]?)(\d{1,3}(?:\.\d{3}){2,})(?:,(\d+))?",
        texte,
    )

    if format_points:
        signe, partie_entiere, decimales = format_points.groups()

        partie_entiere = partie_entiere.replace(".", "")
        montant = signe + partie_entiere

        if decimales is not None:
            montant += "." + decimales

        return float(montant)

    # Autres formats : conserver le convertisseur existant.
    return convertir_montant(texte)


def extraire_valeurs_indicateurs(tableau):
    """Extrait les montants candidats des indicateurs détectés."""

    config = obtenir_configuration(tableau)
    correspondances = trouver_indicateurs(tableau)

    resultats = []

    for correspondance in correspondances:
        cellules = correspondance["texte"].split("|")

        for annee, indice in config["colonnes"].items():
            montant = None

            if indice < len(cellules):
                montant = convertir_montant_financier(
                    cellules[indice]
                )

            resultats.append({
                "table_id": tableau["table_id"],
                "indicator": correspondance["indicator"],
                "observation_year": annee,
                "value": (
                    str(montant)
                    if montant is not None
                    else None
                ),
                "ligne": correspondance["ligne"],
                "texte_source": correspondance["texte"],
                "status": (
                    "CANDIDATE"
                    if montant is not None
                    else "MISSING"
                ),
            })

    return resultats
