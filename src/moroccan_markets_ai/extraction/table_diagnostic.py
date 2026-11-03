from .column_validator import verifier_colonnes
from .table_config import obtenir_configuration


def diagnostiquer_tableau(tableau):
    """Vérifie si les colonnes d'un tableau sont exploitables."""

    resultat = {
        "table_id": tableau["table_id"],
        "entreprise": tableau.get("entreprise"),
        "annee": tableau.get("annee"),
        "pages": tableau.get("pages", []),
        "statut": "TO_REVIEW",
        "raison": None,
        "montants_par_annee": {},
    }

    try:
        config = obtenir_configuration(tableau)
    except ValueError as erreur:
        resultat["raison"] = str(erreur)
        return resultat

    validation = verifier_colonnes(tableau, config)

    resultat["montants_par_annee"] = validation[
        "montants_par_annee"
    ]

    if validation["exploitable"]:
        resultat["statut"] = "READY"
    else:
        resultat["raison"] = "Colonnes sans montants isolés"

    return resultat
