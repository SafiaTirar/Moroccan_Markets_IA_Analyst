import re
import unicodedata


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        caractere for caractere in texte
        if not unicodedata.combining(caractere)
    )
    return texte.upper().strip()


def detecter_colonnes(tableau):
    lignes = tableau["contenu"].splitlines()

    if not lignes:
        return {}

    entete = lignes[0].split(" | ")
    colonnes = {}

    # Année de référence du document, si elle est connue
    annee_reference = tableau.get("annee")

    # 1. Rechercher les années explicites
    for indice, cellule in enumerate(entete):
        annees = re.findall(r"\b20\d{2}\b", cellule)

        if annees:
            # Une seule année dans la cellule :
            # on peut identifier la colonne directement.
            annees_uniques = set(annees)

            if len(annees_uniques) == 1:
                annee = int(annees[0])
                colonnes[annee] = indice

    # 2. Reconnaître EXERCICE et EXERCICE PRECEDENT
    for indice, cellule in enumerate(entete):
        cellule_normale = normaliser(cellule)

        # Retirer le préfixe éventuel ajouté par Docling
        # Exemple : "...2025.EXERCICE PRECEDENT"
        libelle = cellule_normale.split(".")[-1].strip()

        if annee_reference is None:
            continue

        if libelle == "EXERCICE PRECEDENT":
            colonnes[annee_reference - 1] = indice

        elif libelle == "EXERCICE":
            colonnes[annee_reference] = indice

    return colonnes
