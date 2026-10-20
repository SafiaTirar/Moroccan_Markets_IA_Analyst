
import json
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.worldbank.org/v2"

INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": "gdp_growth",
    "FP.CPI.TOTL.ZG": "inflation",
}

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "worldbank"
PROCESSED_DIR = (
    PROJECT_ROOT / "data" / "processed" / "macroeconomic"
)


def fetch_indicator(indicator_code: str) -> list[dict]:
    url = f"{BASE_URL}/country/MAR/indicator/{indicator_code}"

    params = {
        "format": "json",
        "date": "2010:2025",
        "per_page": 1000,
    }

    response = requests.get(
        url,
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if (
        not isinstance(payload, list)
        or len(payload) < 2
        or not isinstance(payload[0], dict)
        or not isinstance(payload[1], list)
    ):
        raise ValueError(
            f"Réponse API inattendue : {indicator_code}"
        )

    metadata, records = payload

    if int(metadata.get("pages", 1)) > 1:
        raise ValueError("Pagination supplémentaire requise")

    if not records:
        raise ValueError(
            f"Aucune observation pour {indicator_code}"
        )

    return records


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    all_data = []

    for code, name in INDICATORS.items():
        print(f"Collecte : {name}")

        records = fetch_indicator(code)

        # Sauvegarder la réponse originale
        raw_file = RAW_DIR / f"{name}.json"

        raw_file.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        for record in records:
            all_data.append({
                "country": "MAR",
                "year": int(record["date"]),
                "indicator_code": code,
                "indicator_name": name,
                "value": record["value"],
                "source": "World Bank",
            })

    df = pd.DataFrame(all_data)

    df["value"] = pd.to_numeric(
        df["value"],
        errors="coerce",
    )

    df = df.sort_values(
        ["indicator_code", "year"]
    )

    df = df.drop_duplicates(
        subset=["country", "year", "indicator_code"]
    )

    output = PROCESSED_DIR / "worldbank_morocco.csv"

    df.to_csv(output, index=False)

    print(f"Collecte terminée : {len(df)} observations")
    print(f"Valeurs manquantes : {df['value'].isna().sum()}")
    print(f"Fichier : {output}")


if __name__ == "__main__":
    main()
