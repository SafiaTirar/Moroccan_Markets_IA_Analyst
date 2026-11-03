import re
import unicodedata


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", str(texte))
    texte = "".join(
        c for c in texte
        if not unicodedata.combining(c)
    )
    texte = texte.upper().replace("’", "'")
    return re.sub(r"\s+", " ", texte).strip()


def detecter_colonnes(tableau):
    lignes = tableau["contenu"].splitlines()[:5]
    annee = tableau.get("annee")

    if not lignes:
        return {}

    # 1. Dates explicites : 2025/2024 ou 31-déc-25
    for ligne in lignes:
        cellules = ligne.split("|")
        colonnes = {}

        for indice, cellule in enumerate(cellules):
            texte = normaliser(cellule)

            annees = re.findall(r"(?<!\d)20\d{2}(?!\d)", texte)

            # Dates abrégées : 31-déc-25 ou 31/12/25
            if not annees:
                dates = re.findall(
                    r"\b\d{1,2}[-/](?:[A-Z]+|\d{1,2})[-/](\d{2})\b",
                    texte,
                )
                annees = ["20" + a for a in dates]

            if len(set(annees)) == 1 and annees:
                colonnes[int(annees[0])] = indice

        if (
            len(colonnes) >= 2
            and len(set(colonnes.values())) == len(colonnes)
        ):
            return colonnes

    if annee is None:
        return {}

    annee = int(annee)

    # 2. Colonnes EXERCICE / EXERCICE PRECEDENT
    for position, ligne in enumerate(lignes):
        cellules = [normaliser(c) for c in ligne.split("|")]

        precedent = []
        courant = []

        for indice, texte in enumerate(cellules):
            if "EXERCICE PRECEDENT" in texte:
                precedent.append(indice)

            elif (
                "TOTAUX DE L'EXERCICE" in texte
                or texte == "EXERCICE"
            ):
                courant.append(indice)

        # Bilan actif : plusieurs colonnes de l'exercice
        # (Brut, Amortissements, Net)
        if precedent and not courant:
            if position + 1 < len(lignes):
                sous_entete = [
                    normaliser(c)
                    for c in lignes[position + 1].split("|")
                ]

                net = [
                    i for i, texte in enumerate(sous_entete)
                    if texte == "NET" and i not in precedent
                ]

                if len(net) == 1:
                    return {
                        annee: net[0],
                        annee - 1: precedent[-1],
                    }

        if len(courant) == 1 and len(precedent) == 1:
            if courant[0] != precedent[0]:
                return {
                    annee: courant[0],
                    annee - 1: precedent[0],
                }

    return {}
