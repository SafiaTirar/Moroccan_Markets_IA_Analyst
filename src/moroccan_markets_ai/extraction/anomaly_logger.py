import json
from pathlib import Path


def enregistrer_anomalies(anomalies, chemin):
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)

    with open(chemin, "w", encoding="utf-8") as fichier:
        json.dump(anomalies, fichier, indent=4, ensure_ascii=False)

    print(f"{len(anomalies)} anomalie(s) enregistrée(s)")
    print(f"Fichier : {chemin}")
