import re
import unicodedata


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        caractere for caractere in texte
        if not unicodedata.combining(caractere)
    )
    return texte.upper().strip()


def detecter_colonne_libelle(tableau):
    lignes = tableau["contenu"].splitlines()

    if not lignes:
        return None

    entete = lignes[0].split(" | ")

    # 1. Rechercher une colonne explicitement nommée
    mots_cles = (
        "DESIGNATION",
        "RUBRIQUE",
        "LIBELLE",
        "INTITULE",
    )

    for indice, cellule in enumerate(entete):
        texte = normaliser(cellule)

        # Docling peut ajouter un préfixe à l'en-tête.
        texte = texte.split(".")[-1].strip()

        if any(mot in texte for mot in mots_cles):
            return indice

    # 2. Examiner les premières lignes de données
    scores = {}

    for ligne in lignes[1:11]:
        cellules = ligne.split(" | ")

        for indice, cellule in enumerate(cellules):
            texte = cellule.strip()

            if not texte:
                continue

            # Une colonne de libellés contient généralement
            # du texte et non uniquement des chiffres.
            lettres = len(re.findall(r"[A-Za-zÀ-ÿ]", texte))
            chiffres = len(re.findall(r"\d", texte))

            if lettres > chiffres:
                scores[indice] = scores.get(indice, 0) + 1

    if not scores:
        return None

    return max(scores, key=scores.get)
