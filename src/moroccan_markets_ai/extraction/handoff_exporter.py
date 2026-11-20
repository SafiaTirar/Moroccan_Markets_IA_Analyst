import json
from decimal import Decimal
from pathlib import Path

from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    load_candidates,
)


def convertir_candidat(candidat):
    """Convertit un candidat vers le format attendu par le FQG."""

    narratif = candidat["type_tableau"] == "NARRATIVE"

    pages = candidat["source_pages"]

    if not pages:
        raise ValueError("Page source manquante")

    page = pages[0]

    if narratif:
        source_table = f"NARRATIVE_P{page:03d}"
        nom = candidat["indicator"]

    else:
        source_table = candidat["source_table"]

        cellules = candidat["ligne_source"].split("|")

        # TotalEnergies : le libellé est dans la deuxième colonne.
        if (
            candidat["entreprise"] == "totalenergies_marketing_maroc"
            and source_table == "DOCLING_003"
        ):
            nom = cellules[1].strip()
        else:
            nom = cellules[0].strip()

    return {
        "entreprise": candidat["entreprise"],
        "nom": nom,
        "indicator": candidat["indicator"],
        "annee": candidat["observation_year"],
        "report_year": candidat["report_year"],
        "valeur": str(Decimal(str(candidat["value"]))),
        "ligne_source": candidat["ligne_source"],
        "unite": candidat.get("unite"),
        "perimetre": candidat.get("perimetre"),
        "source_table": source_table,
        "source_page": page,
        "source_ligne": candidat["source_ligne"],
        "validation_status": "NOT_CHECKED",
    }


def enregistrer_json(chemin, donnees):
    """Enregistre les données JSON en UTF-8."""

    chemin.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def exporter_handoff(candidats, anomalies, bilan, destination):
    """Génère et contrôle un nouveau handoff FQG."""

    destination = Path(destination)

    destination.mkdir(parents=True, exist_ok=False)

    candidats_fqg = [
        convertir_candidat(candidat)
        for candidat in candidats
    ]

    fichier = destination / "candidats_fqg.json"

    enregistrer_json(fichier, candidats_fqg)

    enregistrer_json(
        destination / "anomalies.json",
        anomalies,
    )

    enregistrer_json(
        destination / "bilan.json",
        bilan,
    )

    # Utiliser directement le chargeur officiel du FQG.
    resultat = load_candidates(fichier)

    print("\n=== VALIDATION STRUCTURELLE FQG ===")
    print("Candidats transmis :", resultat.input_count)
    print("Candidats structurellement valides :", len(resultat.loaded))
    print("Candidats rejetes :", len(resultat.rejected))

    for rejet in resultat.rejected:
        print(rejet.input_index, rejet.reasons)

    if resultat.rejected:
        raise ValueError("Certains candidats sont incompatibles avec le FQG")

    print("Export compatible avec le chargeur FQG")

    return resultat
