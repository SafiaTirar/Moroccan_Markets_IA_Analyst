from .indicator_matcher import trouver_indicateurs


def associer_indicateurs(candidats, entreprise):
    """Associe les candidats aux indicateurs officiels."""

    resultats = []

    for candidat in candidats:
        nouveau = candidat.copy()

        tableau_temporaire = {
            "entreprise": entreprise,
            "table_id": candidat.get("source_table", ""),
            "contenu": candidat["nom"],
        }

        correspondances = trouver_indicateurs(tableau_temporaire)

        if len(correspondances) == 1:
            nouveau["indicator"] = correspondances[0]["indicator"]
            nouveau["mapping_status"] = "MATCHED"
        else:
            nouveau["indicator"] = None
            nouveau["mapping_status"] = (
                "AMBIGUOUS" if len(correspondances) > 1
                else "UNMATCHED"
            )

        resultats.append(nouveau)

    return resultats
