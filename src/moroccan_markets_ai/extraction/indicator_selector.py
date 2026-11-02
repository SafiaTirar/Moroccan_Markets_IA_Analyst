import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

REGISTRY_PATH = (
    PROJECT_ROOT
    / "config"
    / "quality"
    / "ammc"
    / "financial_indicator_registry.json"
)


def obtenir_indicateurs(entreprise):
    """Retourne les indicateurs applicables à une entreprise."""
    entreprise = entreprise.strip().lower()
    with REGISTRY_PATH.open(encoding="utf-8") as fichier:
        registre = json.load(fichier)

    profils = registre["company_profiles"]

    if entreprise not in profils:
        raise ValueError(f"Entreprise inconnue : {entreprise}")

    profil = profils[entreprise]

    return [
        {
            "indicator": item["indicator"],
            "synonyms": item["synonyms"],
        }
        for item in registre["indicators"]
        if profil in item["applicable_profiles"]
    ]
