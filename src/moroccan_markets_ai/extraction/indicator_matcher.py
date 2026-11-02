import re
import unicodedata

from .indicator_selector import obtenir_indicateurs


def normaliser(texte):
    """Normalise un texte pour faciliter la recherche."""
    texte = unicodedata.normalize("NFKD", str(texte))
    texte = "".join(
        caractere
        for caractere in texte
        if not unicodedata.combining(caractere)
    )
    texte = texte.lower().replace("’", "'")
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip()


def trouver_indicateurs(tableau):
    """Repère les lignes susceptibles de contenir un indicateur."""

    entreprise = tableau["entreprise"]
    indicateurs = obtenir_indicateurs(entreprise)
    lignes = tableau["contenu"].splitlines()

    correspondances = []

    for numero, ligne in enumerate(lignes):
        ligne_normalisee = normaliser(ligne)

        for item in indicateurs:
            for synonyme in item["synonyms"]:
                if normaliser(synonyme) in ligne_normalisee:
                    correspondances.append({
                        "table_id": tableau["table_id"],
                        "ligne": numero,
                        "indicator": item["indicator"],
                        "synonyme": synonyme,
                        "texte": ligne,
                    })
                    break

    return correspondances
