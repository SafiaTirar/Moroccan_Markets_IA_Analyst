import csv
import hashlib
import json
from pathlib import Path


def charger_tableaux(dossier_rapport):
    """
    Charge les tableaux Docling et OCR d'un rapport
    en conservant leurs métadonnées.
    """
    dossier = Path(dossier_rapport)

    with (dossier / "unified_document.json").open(
        encoding="utf-8"
    ) as fichier:
        document = json.load(fichier)

    # Racine du projet
    racine_projet = Path(__file__).resolve().parents[3]
    racine_ocr = racine_projet / "data/extracted/ammc/ocr_generic"

    tableaux = []
    manquants = 0

    for table in document["tables"]:
        chemin_original = table["csv_path"].replace("\\", "/")
        nom_csv = Path(chemin_original).name

        if table.get("source") == "OCR":
            # Conserver le chemin relatif après ocr_generic
            parties = chemin_original.split("/")

            if "ocr_generic" not in parties:
                manquants += 1
                continue

            position = parties.index("ocr_generic")
            chemin_relatif = Path(*parties[position + 1:])
            chemin_csv = racine_ocr / chemin_relatif

        else:
            chemin_csv = dossier / "tables" / nom_csv

        if not chemin_csv.is_file():
            manquants += 1
            continue

        # Vérifier l'intégrité du fichier
        empreinte_attendue = table.get("sha256")

        if empreinte_attendue:
            empreinte_reelle = hashlib.sha256(
                chemin_csv.read_bytes()
            ).hexdigest()

            if empreinte_reelle != empreinte_attendue:
                print("SHA256 DIFFERENT :", table["table_id"])
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
            "source": table.get("source"),
            "contenu": contenu,
        })

    if manquants:
        print(
            f"ATTENTION : {manquants} CSV manquants "
            f"dans {dossier.parent.name}/{dossier.name}"
        )

    return tableaux
