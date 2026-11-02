from .indicator_matcher import normaliser
from .indicator_selector import obtenir_indicateurs


def associer_indicateurs(candidats, entreprise):
    """Associe les candidats aux indicateurs officiels."""

    indicateurs = obtenir_indicateurs(entreprise)
    resultats = []

    for candidat in candidats:
        libelle = normaliser(candidat["nom"])
        correspondances = []

        for item in indicateurs:
            for synonyme in item["synonyms"]:
                synonyme_normalise = normaliser(synonyme)

                if synonyme_normalise in libelle:
                    correspondances.append(
                        (item["indicator"], len(synonyme_normalise))
                    )

        nouveau_candidat = candidat.copy()

        if correspondances:
            longueur_max = max(
                longueur for _, longueur in correspondances
            )

            meilleurs = {
                indicator
                for indicator, longueur in correspondances
                if longueur == longueur_max
            }

            if len(meilleurs) == 1:
                nouveau_candidat["indicator"] = next(iter(meilleurs))
            else:
                nouveau_candidat["indicator"] = None
                nouveau_candidat["mapping_status"] = "AMBIGUOUS"

        else:
            nouveau_candidat["indicator"] = None
            nouveau_candidat["mapping_status"] = "UNMATCHED"

        resultats.append(nouveau_candidat)

    return resultats
