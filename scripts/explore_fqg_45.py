import json
from collections import Counter, defaultdict
from pathlib import Path

DOSSIER = Path("data/quality/ammc/handoff_fqg_45")


def charger(nom):
    return json.loads((DOSSIER / nom).read_text(encoding="utf-8"))


candidats = charger("candidats_fqg.json")
anomalies = charger("anomalies.json")
bilan = charger("bilan.json")

print("Candidats :", len(candidats))
print("Anomalies :", len(anomalies), "| Bilan :", len(bilan))
print("Entreprises :", len({c["entreprise"] for c in candidats}))

for champ in ["validation_status", "unite", "perimetre", "indicator", "report_year"]:
    print(champ, dict(Counter(c.get(champ) for c in candidats).most_common(20)))

ecarts = Counter(
    c["annee"] - c["report_year"] for c in candidats if c.get("report_year") is not None
)
print("annee - report_year :", dict(sorted(ecarts.items())))

# Doublons : même observation, avec la même valeur ou avec des valeurs différentes
groupes = defaultdict(list)
for c in candidats:
    groupes[(c["entreprise"], c.get("report_year"), c.get("indicator"), c["annee"])].append(c)
memes = sum(
    1 for g in groupes.values() if len(g) > 1 and len({x["valeur"] for x in g}) == 1
)
conflits = [k for k, g in groupes.items() if len({x["valeur"] for x in g}) > 1]
print("Observations répétées avec la même valeur :", memes)
print("Observations avec valeurs différentes :", len(conflits))
for k in conflits[:10]:
    print("  ", k, [x["valeur"] for x in groupes[k]])

print("Raisons des anomalies :", dict(Counter(a["raison"] for a in anomalies)))