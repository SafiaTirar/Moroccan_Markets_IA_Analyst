
import csv
import json
from pathlib import Path


def charger_tableaux(dossier_rapport):
    """
    Charge les tableaux CSV d'un rapport financier
    avec leurs métadonnées.
    """
    dossier = Path(dossier_rapport)
    chemin_json = dossier / "unified_document.json"

    with chemin_json.open("r", encoding="utf-8") as fichier:
        document = json.load(fichier)

    tableaux = []

    for table in document["tables"]:
        # Récupérer le nom du CSV, sans utiliser
        # le chemin absolu de l'ordinateur de la collègue.
        nom_csv = Path(
            table["csv_path"].replace("\\", "/")
        ).name

        chemin_csv = dossier / "tables" / nom_csv

        if not chemin_csv.is_file():
            print(f"CSV introuvable : {chemin_csv}")
            continue

        with chemin_csv.open(
            "r", encoding="utf-8-sig", newline=""
        ) as fichier:
            lignes = list(csv.reader(fichier))

        contenu = "\n".join(
            " | ".join(ligne) for ligne in lignes
        )

        tableaux.append({
            "table_id": table["table_id"],
            "pages": table["page"],
            "qualite_structurelle": table["quality_status"],
            "contenu": contenu,
        })

    return tableaux
