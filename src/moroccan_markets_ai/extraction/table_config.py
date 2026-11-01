from .column_detector import detecter_colonnes

CONFIG_TABLEAUX = {
    "DOCLING_018": {
        "type": "CPC",
        "colonnes": {2025: 2, 2024: 4},
        "colonnes_signes": {2025: 1, 2024: 3}
    },
    "DOCLING_020": {
        "type": "BILAN_PASSIF",
        "colonnes": {2025: 1, 2024: 2},
        "colonnes_signes": {}
    }
}


def obtenir_configuration(tableau):
    table_id = tableau["table_id"]

    # Priorité aux configurations déjà vérifiées
    if table_id in CONFIG_TABLEAUX:
        return CONFIG_TABLEAUX[table_id]

    # Détection automatique pour les autres tableaux
    colonnes = detecter_colonnes(tableau)

    if not colonnes:
        raise ValueError(
            f"Colonnes non détectées pour {table_id}"
        )

    return {
        "type": "AUTO_DETECTE",
        "colonnes": colonnes,
        "colonnes_signes": {}
    }
