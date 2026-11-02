import unicodedata


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        c for c in texte
        if not unicodedata.combining(c)
    )
    return texte.upper().strip()


def detecter_perimetre_titre(titre):
    titre = normaliser(titre)

    # Reconnaître les sections de comptes consolidés
    if (
        "ETATS FINANCIERS CONSOLIDES" in titre
        or "COMPTES CONSOLIDES" in titre
        or "BILAN CONSOLIDE" in titre
    ):
        return "CONSOLIDE"

    # Reconnaître les sections de comptes sociaux
    if (
        "ETATS FINANCIERS SOCIAUX" in titre
        or "COMPTES SOCIAUX" in titre
        or "ETATS DE SYNTHESE SOCIAUX" in titre
    ):
        return "SOCIAL"

    return "UNKNOWN"



def detecter_perimetre_tableau(contenu_tableau, document_md):

    def cellules(ligne):
        return [
            normaliser(cellule)
            for cellule in ligne.strip().strip("|").split("|")
        ]

    # Lignes significatives du tableau CSV
    lignes_tableau = [
        cellules(ligne)
        for ligne in contenu_tableau.splitlines()
        if "|" in ligne
    ]

    # Lignes du document Markdown
    lignes_document = document_md.splitlines()

    # Rechercher la meilleure correspondance
    meilleur_score = 0
    meilleure_position = None

    for i, ligne in enumerate(lignes_document):

        if "|" not in ligne:
            continue

        ligne_doc = cellules(ligne)

        score = 0

        for ligne_tableau in lignes_tableau:

            if ligne_tableau == ligne_doc:
                score += 1

        if score > meilleur_score:
            meilleur_score = score
            meilleure_position = i

    if meilleur_score == 0:
        return "UNKNOWN"

    # Rechercher le périmètre avant le tableau
    for j in range(meilleure_position, -1, -1):

        titre = lignes_document[j].strip()

        if titre.startswith("#"):
            perimetre = detecter_perimetre_titre(titre)

            if perimetre != "UNKNOWN":
                return perimetre

    return "UNKNOWN"
