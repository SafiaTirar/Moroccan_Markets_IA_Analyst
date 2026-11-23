import json
from pathlib import Path

from extract_ammc_45 import traiter_rapport

from moroccan_markets_ai.extraction.handoff_exporter import (
    exporter_handoff,
)


def main():
    configuration = json.loads(
        Path("config/data/ammc_corpus.json").read_text(
            encoding="utf-8"
        )
    )

    tous_candidats = []
    toutes_anomalies = []
    bilan = []

    for compagnie in configuration["companies"]:
        entreprise = compagnie["company_id"]

        for annee in configuration["target_years"]:
            candidats, anomalies, financiers, ready = traiter_rapport(
                entreprise,
                annee,
            )

            tous_candidats.extend(candidats)

            for anomalie in anomalies:
                toutes_anomalies.append({
                    "entreprise": entreprise,
                    "report_year": annee,
                    **anomalie,
                })

            bilan.append({
                "entreprise": entreprise,
                "annee": annee,
                "tableaux_financiers": financiers,
                "tableaux_ready": ready,
                "candidats": len(candidats),
                "anomalies": len(anomalies),
            })

            print(
                entreprise,
                annee,
                "| Candidats :", len(candidats),
                "| Anomalies :", len(anomalies),
            )

    print("\n=== BILAN GLOBAL V2 ===")
    print("Rapports traites :", len(bilan))
    print("Candidats :", len(tous_candidats))
    print("Anomalies :", len(toutes_anomalies))

    exporter_handoff(
    candidats=tous_candidats,
    anomalies=toutes_anomalies,
    bilan=bilan,
    destination="data/quality/ammc/handoff_fqg_v2_final",
    fichier_historique="data/quality/ammc/handoff_fqg_45/candidats_fqg.json",
    )


if __name__ == "__main__":
    main()
