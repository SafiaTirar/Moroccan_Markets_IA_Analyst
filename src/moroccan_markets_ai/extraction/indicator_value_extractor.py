import re

from .indicator_matcher import trouver_indicateurs
from .line_extractor import convertir_montant
from .table_config import obtenir_configuration


def convertir_montant_financier(cellule, point_milliers=False):
    """Convertit un montant financier en respectant son format."""

    texte = str(cellule).strip()

    # Cas : 9.332.718, -1.588.764 ou 1.588.764,25
    format_points = re.fullmatch(
        r"([+-]?)(\d{1,3}(?:\.\d{3}){2,})(?:,(\d+))?",
        texte,
    )

    if format_points:
        signe, partie_entiere, decimales = format_points.groups()
        montant = signe + partie_entiere.replace(".", "")

        if decimales is not None:
            montant += "." + decimales

        return float(montant)

    # Cas : 85.712 (un seul point)
    # À utiliser uniquement si le tableau emploie
    # le point comme séparateur de milliers.
    if point_milliers and re.fullmatch(
        r"[+-]?\d{1,3}\.\d{3}",
        texte,
    ):
        return float(texte.replace(".", ""))

    # Autres formats : conserver le comportement existant.
    return convertir_montant(texte)
def extraire_valeurs_indicateurs(tableau):
    """Extrait les montants candidats des indicateurs détectés."""

    config = obtenir_configuration(tableau)
    correspondances = trouver_indicateurs(tableau)
    point_milliers = tableau.get("point_milliers", False)
    resultats = []

    for correspondance in correspondances:
        cellules = correspondance["texte"].split("|")

        for annee, indice in config["colonnes"].items():
            montant = None

            if indice < len(cellules):
                montant = convertir_montant_financier(
                    cellules[indice],
                    point_milliers=point_milliers,

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
