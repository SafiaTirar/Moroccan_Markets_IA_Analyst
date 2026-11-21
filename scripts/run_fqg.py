"""Lance le Financial Quality Gate sur un fichier de candidats.

Sans argument : traite les 45 rapports (candidats_fqg.json + anomalies.json).
Avec un fichier : uv run python scripts/run_fqg.py chemin/candidats.json [chemin/anomalies.json]
"""

import sys
from pathlib import Path

from moroccan_markets_ai.quality_gate import (
    charger_json,
    ecrire_decisions,
    evaluer,
    resume,
)

DOSSIER_45 = Path("data/quality/ammc/handoff_fqg_45")
SORTIE = Path("data/quality/ammc/fqg_resultats")


def main() -> None:
    args = sys.argv[1:]
    if args:
        chemin_candidats = Path(args[0])
        chemin_anomalies = Path(args[1]) if len(args) > 1 else None
    else:
        chemin_candidats = DOSSIER_45 / "candidats_fqg.json"
        chemin_anomalies = DOSSIER_45 / "anomalies.json"

    candidats = charger_json(chemin_candidats)
    anomalies = charger_json(chemin_anomalies) if chemin_anomalies else []
    decisions = evaluer(candidats, anomalies)

    sortie = SORTIE / f"decisions_{chemin_candidats.stem}.json"
    ecrire_decisions(decisions, sortie)

    bilan = resume(decisions)
    print("Candidats :", bilan["total"])
    for decision, nombre in sorted(bilan["decisions"].items()):
        print(f"  {decision} : {nombre}")
    print("Contrôles en échec :")
    for controle, nombre in sorted(
        bilan["controles_en_echec"].items(), key=lambda x: -x[1]
    ):
        print(f"  {controle} : {nombre}")
    print("Résultats écrits dans", sortie)


if __name__ == "__main__":
    main()
