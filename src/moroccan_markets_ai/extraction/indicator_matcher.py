import re
import unicodedata

from .indicator_selector import obtenir_indicateurs
from .table_detector import detecter_type_tableau


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
    """Détecte les indicateurs financiers en limitant les faux positifs."""

    indicateurs = obtenir_indicateurs(tableau["entreprise"])
    type_tableau = detecter_type_tableau(tableau)

    # Les capitaux propres sont un poste du passif,
    # pas des actifs financiers.
    if type_tableau == "BILAN_ACTIF":
        indicateurs = [
            item
            for item in indicateurs
            if item["indicator"] != "EQUITY"
        ]

    lignes = tableau["contenu"].splitlines()
    correspondances = []

    for numero, ligne in enumerate(lignes):
        # Ne rechercher que dans le libellé de la ligne.
        libelle = ligne.split("|")[0]
        texte = normaliser(libelle)

        # Exclure les composantes qui ne représentent
        # pas le total des capitaux propres.
        if (
            "capitaux propres assimiles" in texte
            or "gains et pertes comptabilises directement en capitaux propres" in texte
        ):
            continue

        # Distinguer le résultat net total
        # du résultat net part du groupe.
        if "resultat net" in texte and "part du groupe" in texte:
            indicator = "NET_INCOME_GROUP_SHARE"
            synonyme = "résultat net ... part du groupe"

        elif "resultat net des entreprises integrees" in texte:
            # Ce poste nécessite une vérification comptable.
            continue

        else:
            candidats = []

            for item in indicateurs:
                for synonyme_possible in item["synonyms"]:
                    synonyme_normalise = normaliser(synonyme_possible)

                    if synonyme_normalise in texte:
                        candidats.append(
                            (
                                item["indicator"],
                                synonyme_possible,
                                len(synonyme_normalise),
                            )
                        )

            if not candidats:
                continue

            # Privilégier le synonyme le plus spécifique.
            longueur_max = max(
                longueur
                for _, _, longueur in candidats
            )

            meilleurs = [
                (indicator, synonyme)
                for indicator, synonyme, longueur in candidats
                if longueur == longueur_max
            ]

            # Ignorer les correspondances ambiguës.
            if len({item[0] for item in meilleurs}) != 1:
                continue

            indicator, synonyme = meilleurs[0]

        correspondances.append({
            "table_id": tableau["table_id"],
            "ligne": numero,
            "indicator": indicator,
            "synonyme": synonyme,
            "texte": ligne,
        })

    return correspondances
