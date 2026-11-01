import json
from pathlib import Path

# Dossier contenant les résultats des extractions
dossier = Path("data/quality/ammc/S2M_2025")

# Fichiers à regrouper
fichiers = [
    "candidats_valides.json",       # CPC
    "candidats_DOCLING_019.json",   # Bilan Actif
    "candidats_DOCLING_020.json",   # Bilan Passif
]

tous_les_candidats = []

for nom_fichier in fichiers:
    chemin = dossier / nom_fichier

    # Vérifier que le fichier existe
    if not chemin.exists():
        raise FileNotFoundError(
            f"Fichier introuvable : {chemin}"
        )

    # Charger les candidats
    with open(chemin, "r", encoding="utf-8") as fichier:
        candidats = json.load(fichier)

    if not isinstance(candidats, list):
        raise ValueError(  # noqa: TRY004
            f"Format invalide dans {nom_fichier} : liste attendue"
        )

    print(nom_fichier, ":", len(candidats), "candidats")

    tous_les_candidats.extend(candidats)

# Vérifier le nombre total attendu pour ce test S2M
if len(tous_les_candidats) != 192:
    raise ValueError(
        f"Total inattendu : {len(tous_les_candidats)} "
        "candidats au lieu de 192"
    )

# Sauvegarder le résultat consolidé
sortie = dossier / "candidats_consolides.json"

with open(sortie, "w", encoding="utf-8") as fichier:
    json.dump(
        tous_les_candidats,
        fichier,
        ensure_ascii=False,
        indent=4,
    )

print("\n--- CONSOLIDATION TERMINÉE ---")
print("Total des candidats :", len(tous_les_candidats))
print("Fichier créé :", sortie)
print("Statut : candidats prévalidés, non encore VERIFIED")
