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
