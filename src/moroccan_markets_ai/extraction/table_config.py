from .column_detector import detecter_colonnes

CONFIG_TABLEAUX = {
    ("S2M", 2025, "DOCLING_018"): {
        "type": "CPC",
        "colonnes": {2025: 2, 2024: 4},
        "colonnes_signes": {2025: 1, 2024: 3}
    },
    ("S2M", 2025, "DOCLING_020"): {
        "type": "BILAN_PASSIF",
        "colonnes": {2025: 1, 2024: 2},
        "colonnes_signes": {}
    }
}


def obtenir_configuration(tableau):
    cle = (
        tableau.get("entreprise"),
        tableau.get("annee"),
        tableau["table_id"]
    )

    # Utiliser uniquement les configurations vérifiées
    if cle in CONFIG_TABLEAUX:
        return CONFIG_TABLEAUX[cle]

    # Sinon, détecter automatiquement les colonnes
    colonnes = detecter_colonnes(tableau)

    if not colonnes:
        raise ValueError(
            f"Colonnes non détectées pour {cle}"
        )

    return {
        "type": "AUTO_DETECTE",
        "colonnes": colonnes,
        "colonnes_signes": {}
    }
