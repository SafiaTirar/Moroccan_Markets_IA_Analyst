import re


def est_montant(texte):
    """Vérifie si une cellule contient un montant isolé."""

    texte = str(texte).strip()
    texte = texte.replace("\u202f", "")
    texte = texte.replace("\u00a0", "")

    if not texte:
        return False

    return bool(re.fullmatch(
        r"-?\(?\d[\d .]*(?:,\d+)?\)?",
        texte
    ))


def verifier_colonnes(tableau, config):
    """Vérifie la présence de montants dans les colonnes configurées."""

    lignes = tableau["contenu"].splitlines()[1:]
    colonnes = config["colonnes"]

    resultats = {}

    for annee, indice in colonnes.items():
        nombre_montants = 0

        for ligne in lignes:
            cellules = ligne.split(" | ")

            if indice < len(cellules):  # noqa: SIM102
                if est_montant(cellules[indice]):
                    nombre_montants += 1

        resultats[annee] = nombre_montants

    return {
        "exploitable": bool(resultats) and all(
            nombre > 0 for nombre in resultats.values()
        ),
        "montants_par_annee": resultats,
    }
