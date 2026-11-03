def filtrer_candidats(candidats):
    """Sépare les indicateurs reconnus des autres candidats."""

    retenus = []
    exclus = []

    for candidat in candidats:
        if candidat.get("indicator") is not None:
            retenus.append(candidat)
        else:
            exclus.append(candidat)

    return retenus, exclus


def regrouper_doublons(candidats):
    """
    Regroupe les doublons potentiels au sein d'un même tableau.
    Conserve la provenance des lignes alternatives.
    """

    groupes = {}

    for candidat in candidats:
        cle = (
            candidat.get("table_id"),
            candidat.get("indicator"),
            candidat.get("observation_year"),
            candidat.get("value"),
        )

        groupes.setdefault(cle, []).append(candidat)

    resultats = []

    for observations in groupes.values():

        # Privilégier le libellé exact CAPITAUX PROPRES.
        def priorite(candidat):
            libelle = candidat.get("texte_source", "").split("|")[0]
            libelle = libelle.strip().upper()

            return (
                libelle != "CAPITAUX PROPRES",
                candidat.get("ligne", 0),
            )

        observations = sorted(observations, key=priorite)

        principal = observations[0].copy()

        principal["sources_alternatives"] = [
            {
                "ligne": autre.get("ligne"),
                "texte_source": autre.get("texte_source"),
            }
            for autre in observations[1:]
        ]

        principal["doublon_potentiel"] = len(observations) > 1

        resultats.append(principal)

    return resultats
