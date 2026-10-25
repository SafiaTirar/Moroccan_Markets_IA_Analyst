"""Normalisation et contrôle qualité des séries annuelles World Bank du Maroc."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.collectors.worldbank_collector import (
    COUNTRY,
    END_YEAR,
    INDICATORS,
    MANIFEST_FILE,
    PROJECT_ROOT,
    RAW_DIR,
    START_YEAR,
    validate_payload,
)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed" / "macroeconomic"
OUTPUT_FILE = PROCESSED_DIR / "worldbank_morocco_annual.csv"
QUALITY_FILE = PROCESSED_DIR / "worldbank_quality_report.json"
PROCESSOR_SCHEMA_VERSION = "1.0"
PRIORITY_YEARS = {2023, 2024, 2025}
OUTPUT_COLUMNS = [
    "country",
    "year",
    "indicator",
    "value",
    "unit",
    "source",
    "source_file",
    "source_sha256",
]


class WorldBankQualityError(ValueError):
    """Erreur de provenance ou de structure empêchant une sortie fiable."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def parse_value(value: Any, *, code: str, year: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise WorldBankQualityError(f"Valeur booléenne pour {code}, {year}")
    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise WorldBankQualityError(
            f"Valeur non numérique pour {code}, {year}: {value}"
        ) from exc
    return format(numeric, "f")


def process_worldbank(
    *,
    raw_dir: Path = RAW_DIR,
    output_file: Path = OUTPUT_FILE,
    quality_file: Path = QUALITY_FILE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = raw_dir / MANIFEST_FILE.name
    if not manifest_path.is_file():
        raise WorldBankQualityError(f"Manifeste absent : {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorldBankQualityError("Manifeste World Bank JSON invalide") from exc

    period = manifest.get("requested_period", {})
    start_year = int(period.get("start_year", START_YEAR))
    end_year = int(period.get("end_year", END_YEAR))
    entries = manifest.get("indicators")
    if not isinstance(entries, list):
        raise WorldBankQualityError("Entrées d'indicateurs absentes du manifeste")
    by_code = {
        entry.get("indicator_code"): entry
        for entry in entries
        if isinstance(entry, dict)
    }
    if set(by_code) != set(INDICATORS):
        raise WorldBankQualityError(
            "Le manifeste ne contient pas exactement les indicateurs attendus"
        )

    rows: list[dict[str, Any]] = []
    indicator_reports: list[dict[str, Any]] = []
    expected_years = set(range(start_year, end_year + 1))
    for code, definition in INDICATORS.items():
        entry = by_code[code]
        raw_path = Path(str(entry.get("raw_file", "")))
        if not raw_path.is_absolute():
            raw_path = raw_dir / raw_path
        if not raw_path.is_file():
            raise WorldBankQualityError(f"Fichier brut absent : {raw_path}")
        actual_sha = sha256_file(raw_path)
        if actual_sha != entry.get("sha256"):
            raise WorldBankQualityError(f"SHA-256 invalide : {raw_path}")
        try:
            payload = json.loads(
                raw_path.read_text(encoding="utf-8"), parse_float=Decimal
            )
            _, records = validate_payload(payload, code)
        except (json.JSONDecodeError, RuntimeError) as exc:
            raise WorldBankQualityError(
                f"Réponse brute invalide pour {code}: {exc}"
            ) from exc

        years: list[int] = []
        null_years: list[int] = []
        for record in records:
            try:
                year = int(record["date"])
            except (KeyError, TypeError, ValueError) as exc:
                raise WorldBankQualityError(
                    f"Année invalide pour {code}: {record.get('date')}"
                ) from exc
            if year not in expected_years:
                raise WorldBankQualityError(
                    f"Année {year} hors période demandée pour {code}"
                )
            api_unit = str(record.get("unit") or "").strip()
            if api_unit and api_unit.lower() not in {"%", "percent", "percentage"}:
                raise WorldBankQualityError(
                    f"Unité API incompatible pour {code}: {api_unit}"
                )
            value = parse_value(record.get("value"), code=code, year=year)
            if not value:
                null_years.append(year)
            years.append(year)
            rows.append(
                {
                    "country": COUNTRY,
                    "year": year,
                    "indicator": definition["indicator"],
                    "value": value,
                    "unit": definition["unit"],
                    "source": entry["url"],
                    "source_file": str(raw_path.resolve()),
                    "source_sha256": actual_sha,
                }
            )
        duplicates = sorted(year for year, count in Counter(years).items() if count > 1)
        if duplicates:
            raise WorldBankQualityError(
                f"Années dupliquées pour {code}: {duplicates}"
            )
        missing_years = sorted(expected_years - set(years))
        indicator_reports.append(
            {
                "indicator_code": code,
                "indicator": definition["indicator"],
                "official_name": definition["official_name"],
                "unit": definition["unit"],
                "unit_basis": "official indicator name and definition",
                "observation_count": len(records),
                "available_years": sorted(years),
                "missing_years": missing_years,
                "null_value_years": sorted(null_years),
                "duplicate_years": duplicates,
                "source_file": str(raw_path.resolve()),
                "source_sha256": actual_sha,
                "api_last_updated": entry.get("api_last_updated"),
            }
        )

    keys = [(row["country"], row["year"], row["indicator"]) for row in rows]
    duplicate_keys = [key for key, count in Counter(keys).items() if count > 1]
    if duplicate_keys:
        raise WorldBankQualityError(f"Observations dupliquées : {duplicate_keys}")
    rows.sort(key=lambda item: (item["year"], item["indicator"]))
    missing_count = sum(len(item["missing_years"]) for item in indicator_reports)
    null_count = sum(len(item["null_value_years"]) for item in indicator_reports)
    priority = [
        {
            "country": row["country"],
            "year": row["year"],
            "indicator": row["indicator"],
            "value": row["value"],
            "unit": row["unit"],
        }
        for row in rows
        if row["year"] in PRIORITY_YEARS
    ]
    quality = {
        "schema_version": PROCESSOR_SCHEMA_VERSION,
        "status": "PASSED" if missing_count == 0 and null_count == 0 else "REVIEW_REQUIRED",
        "country": COUNTRY,
        "period": {"start_year": start_year, "end_year": end_year},
        "observation_count": len(rows),
        "missing_year_count": missing_count,
        "null_value_count": null_count,
        "duplicate_count": 0,
        "indicators": indicator_reports,
        "priority_2023_2025": priority,
        "semantic_warning": (
            "World Bank annual consumer-price inflation is an annual percentage "
            "change; it is not interchangeable with HCP monthly CPI index points."
        ),
        "interpolation_applied": False,
        "output_file": str(output_file.resolve()),
    }
    atomic_csv(output_file, rows)
    atomic_json(quality_file, quality)
    return rows, quality


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-file", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--quality-file", type=Path, default=QUALITY_FILE)
    args = parser.parse_args()
    _, quality = process_worldbank(
        raw_dir=args.raw_dir,
        output_file=args.output_file,
        quality_file=args.quality_file,
    )
    print(json.dumps(quality, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
