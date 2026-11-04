import json
from collections import Counter, defaultdict
from pathlib import Path

CHEMIN = Path("data/quality/ammc/S2M_2025/candidats_consolides.json")

candidats = json.loads(CHEMIN.read_text(encoding="utf-8"))
print("Total :", len(candidats))

for champ in ["validation_status", "source_table", "annee", "unite", "perimetre"]:
    print(champ, dict(Counter(c.get(champ) for c in candidats)))

# Doublons exacts sur la même ligne et la même année
cles = Counter(
    (c["source_table"], c["source_ligne"], c["nom"], c["annee"]) for c in candidats
)
doublons = {k: n for k, n in cles.items() if n > 1}
print("Doublons :", len(doublons))

# Lignes qui n'ont pas exactement 2 années
par_ligne = defaultdict(list)
for c in candidats:
    par_ligne[(c["source_table"], c["source_ligne"])].append(c)
anormales = {k: [c["annee"] for c in v] for k, v in par_ligne.items() if len(v) != 2}
print("Lignes sans 2 années :", len(anormales))

# Valeurs à surveiller
print("Valeurs nulles :", sum(1 for c in candidats if c["valeur"] == 0))
print("Valeurs négatives :", sum(1 for c in candidats if c["valeur"] < 0))

print("\n--- Lignes sans 2 années ---")
for (table, ligne), annees in sorted(anormales.items()):
    c = par_ligne[(table, ligne)][0]
    print(table, ligne, annees, c["nom"])

print("\n--- Valeurs négatives ---")
for c in candidats:
    if c["valeur"] < 0:
        print(c["source_table"], c["source_ligne"], c["annee"], c["valeur"], c["nom"])