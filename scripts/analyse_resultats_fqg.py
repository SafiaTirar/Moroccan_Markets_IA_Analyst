"""Résume les décisions du Financial Quality Gate (après scripts/run_fqg.py)."""

import json
from collections import Counter
from pathlib import Path

CHEMIN = Path("data/quality/ammc/fqg_resultats/decisions_candidats_fqg.json")
MAX_EXEMPLES = 5

decisions = json.loads(CHEMIN.read_text(encoding="utf-8"))
revues = [d for d in decisions if d["decision"] == "REVIEW_REQUIRED"]

print("Décisions par indicateur :")
par_indicateur = Counter((d["indicateur"], d["decision"]) for d in decisions)
for (indicateur, decision), nombre in sorted(par_indicateur.items()):
    print(f"  {indicateur:32} {decision:16} {nombre}")

print("\nREVIEW_REQUIRED par entreprise :")
for entreprise, nombre in Counter(d["entreprise"] for d in revues).most_common():
    print(f"  {entreprise} : {nombre}")


def cle_version(d):
    return (d["entreprise"], d["report_year"], d["annee"], d["indicateur"])


divergences = [
    d
    for d in revues
    if any(c["nom"] == "divergence" and c["niveau"] == "REVUE" for c in d["controles"])
]
cles = list(dict.fromkeys(cle_version(d) for d in divergences))

print(f"\nDivergences : {len(cles)} versions concernées, exemples :")
for cle in cles[:MAX_EXEMPLES]:
    print(cle)
    for d in divergences:
        if cle_version(d) == cle:
            print(
                f"   {d['valeur']:>20,.2f}  {d['source_table']} "
                f"p.{d['source_page']} l.{d['source_ligne']}  {d['ligne_source'][:60]}"
            )
