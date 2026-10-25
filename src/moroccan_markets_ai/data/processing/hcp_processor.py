"""Normalisation et contrôle qualité de l'IPC mensuel du HCP."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from moroccan_markets_ai.data.collectors.hcp_collector import (
    METADATA_FILE,
    PROJECT_ROOT,
    RAW_DIR,
    RAW_WORKBOOK,
    SOURCE_PAGE_URL,
)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed" / "macroeconomic"
OUTPUT_FILE = PROCESSED_DIR / "hcp_ipc_monthly.csv"
STRUCTURE_FILE = PROCESSED_DIR / "hcp_ipc_workbook_structure.json"
QUALITY_FILE = PROCESSED_DIR / "hcp_ipc_quality_report.json"
PROCESSOR_SCHEMA_VERSION = "1.0"
INDICATOR = "consumer_price_index"
UNIT = "index_points"
PRIORITY_YEARS = {2023, 2024, 2025}

OUTPUT_COLUMNS = [
    "date",
    "indicator",
    "category",
    "value",
    "unit",
    "base_year",
    "source",
    "source_file",
    "source_sha256",
    "source_sheet",
    "source_row",
]


class HCPQualityError(ValueError):
    """Erreur structurelle empêchant une normalisation fiable."""


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_value).strip().lower()


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


def find_header(sheet: Any) -> tuple[int, int, list[str]]:
    for row_index in range(1, min(sheet.max_row, 60) + 1):
        values = [sheet.cell(row_index, column).value for column in range(1, sheet.max_column + 1)]
        normalized = [normalize_text(value) for value in values]
        if "mois" not in normalized:
            continue
        month_column = normalized.index("mois") + 1
        categories = [
            str(sheet.cell(row_index, column).value).strip()
            for column in range(month_column + 1, sheet.max_column + 1)
            if sheet.cell(row_index, column).value not in (None, "")
        ]
        if categories:
            return row_index, month_column, categories
    raise HCPQualityError("En-tête 'Mois' et catégories introuvables")


def parse_month(value: Any) -> date | None:
    match = re.fullmatch(r"(20\d{2})/(0[1-9]|1[0-2])", str(value or "").strip())
    if not match:
        return None
    return date(int(match.group(1)), int(match.group(2)), 1)


def parse_numeric(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise HCPQualityError(f"Valeur booléenne inattendue : {value}")
    normalized = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise HCPQualityError(f"Valeur IPC non numérique : {value}") from exc


def month_sequence(start: date, end: date) -> list[date]:
    months: list[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return months


def inspect_workbook(path: Path) -> dict[str, Any]:
    workbook = load_workbook(path, data_only=True, read_only=False)
    sheets: list[dict[str, Any]] = []
    for sheet in workbook.worksheets:
        header_row, month_column, categories = find_header(sheet)
        data_rows = [
            row
            for row in range(header_row + 1, sheet.max_row + 1)
            if parse_month(sheet.cell(row, month_column).value)
        ]
        source_notes = [
            str(sheet.cell(row, month_column).value).strip()
            for row in range(header_row + 1, sheet.max_row + 1)
            if normalize_text(sheet.cell(row, month_column).value).startswith("source")
        ]
        sheets.append(
            {
                "name": sheet.title,
                "max_row": sheet.max_row,
                "max_column": sheet.max_column,
                "header_row": header_row,
                "month_column": month_column,
                "categories": categories,
                "data_start_row": min(data_rows) if data_rows else None,
                "data_end_row": max(data_rows) if data_rows else None,
                "data_row_count": len(data_rows),
                "merged_cells": [str(cell_range) for cell_range in sheet.merged_cells.ranges],
                "hidden_rows": [
                    row for row, dimension in sheet.row_dimensions.items() if dimension.hidden
                ],
                "hidden_columns": [
                    column
                    for column, dimension in sheet.column_dimensions.items()
                    if dimension.hidden
                ],
                "source_notes": source_notes,
            }
        )
    return {"sheet_count": len(sheets), "sheets": sheets}


def extract_observations(
    workbook_path: Path,
    *,
    source_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workbook = load_workbook(workbook_path, data_only=True, read_only=False)
    if len(workbook.worksheets) != 1:
        raise HCPQualityError(
            f"Une feuille attendue, {len(workbook.worksheets)} trouvées"
        )
    sheet = workbook.active
    header_row, month_column, categories = find_header(sheet)
    if len(categories) != 3:
        raise HCPQualityError(
            f"Trois catégories IPC attendues, {len(categories)} trouvées"
        )
    base_years = {int(year) for year in re.findall(r"20\d{2}", sheet.title)}
    if len(base_years) != 1:
        raise HCPQualityError("Année de base absente ou ambiguë dans le nom de feuille")
    base_year = base_years.pop()

    observations: list[dict[str, Any]] = []
    missing_cells: list[dict[str, Any]] = []
    invalid_period_rows: list[int] = []
    seen_data = False
    for row_index in range(header_row + 1, sheet.max_row + 1):
        raw_period = sheet.cell(row_index, month_column).value
        period = parse_month(raw_period)
        values = [
            sheet.cell(row_index, month_column + offset).value
            for offset in range(1, len(categories) + 1)
        ]
        if period is None:
            if seen_data and any(value not in (None, "") for value in values):
                invalid_period_rows.append(row_index)
            continue
        seen_data = True
        for offset, category in enumerate(categories, start=1):
            numeric = parse_numeric(sheet.cell(row_index, month_column + offset).value)
            if numeric is None:
                missing_cells.append(
                    {
                        "date": period.isoformat(),
                        "category": category,
                        "source_row": row_index,
                    }
                )
            observations.append(
                {
                    "date": period.isoformat(),
                    "indicator": INDICATOR,
                    "category": category,
                    "value": "" if numeric is None else format(numeric, "f"),
                    "unit": UNIT,
                    "base_year": base_year,
                    "source": SOURCE_PAGE_URL,
                    "source_file": str(workbook_path.resolve()),
                    "source_sha256": source_sha256,
                    "source_sheet": sheet.title,
                    "source_row": row_index,
                }
            )

    if invalid_period_rows:
        raise HCPQualityError(
            f"Lignes de données avec période invalide : {invalid_period_rows}"
        )
    observations.sort(key=lambda item: (item["date"], item["category"]))
    keys = [(item["date"], item["indicator"], item["category"]) for item in observations]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    if duplicates:
        raise HCPQualityError(f"Observations dupliquées : {duplicates[:5]}")

    dates = sorted({date.fromisoformat(item["date"]) for item in observations})
    expected_dates = month_sequence(dates[0], dates[-1]) if dates else []
    missing_months = sorted(set(expected_dates) - set(dates))
    category_counts = Counter(item["category"] for item in observations)
    quality = {
        "observation_count": len(observations),
        "category_count": len(categories),
        "categories": categories,
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "month_count": len(dates),
        "expected_month_count": len(expected_dates),
        "missing_months": [month.isoformat() for month in missing_months],
        "missing_value_count": len(missing_cells),
        "missing_values": missing_cells,
        "duplicate_count": 0,
        "category_observation_counts": dict(category_counts),
        "priority_2023_2025_observations": sum(
            date.fromisoformat(item["date"]).year in PRIORITY_YEARS
            for item in observations
        ),
        "unit": UNIT,
        "base_year": base_year,
        "frequency": "monthly",
        "indicator_semantics": (
            "Niveau de l'indice des prix à la consommation; "
            "ce champ n'est pas un taux d'inflation."
        ),
    }
    return observations, quality


def process_hcp_ipc(
    *,
    raw_dir: Path = RAW_DIR,
    output_dir: Path = PROCESSED_DIR,
) -> dict[str, Any]:
    workbook_path = raw_dir / RAW_WORKBOOK.name
    metadata_path = raw_dir / METADATA_FILE.name
    if not workbook_path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError("Collecte HCP absente : exécuter hcp_collector d'abord")
    collection_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    actual_sha = sha256_file(workbook_path)
    if actual_sha != collection_metadata.get("sha256"):
        raise HCPQualityError("SHA-256 du classeur différent des métadonnées de collecte")

    structure = inspect_workbook(workbook_path)
    observations, quality = extract_observations(
        workbook_path,
        source_sha256=actual_sha,
    )
    output_file = output_dir / OUTPUT_FILE.name
    structure_file = output_dir / STRUCTURE_FILE.name
    quality_file = output_dir / QUALITY_FILE.name
    atomic_csv(output_file, observations)
    atomic_json(structure_file, structure)

    status = (
        "VALID"
        if not quality["missing_months"] and not quality["missing_value_count"]
        else "REVIEW_REQUIRED"
    )
    report = {
        "schema_version": PROCESSOR_SCHEMA_VERSION,
        "status": status,
        "source_page_url": SOURCE_PAGE_URL,
        "raw_file": str(workbook_path.resolve()),
        "raw_sha256": actual_sha,
        "output_file": str(output_file.resolve()),
        "structure_file": str(structure_file.resolve()),
        "quality": quality,
        "rules": {
            "interpolation": "FORBIDDEN",
            "duplicate_handling": "FAIL",
            "invalid_period_handling": "FAIL",
            "missing_values": "PRESERVE_AND_REPORT",
        },
    }
    atomic_json(quality_file, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=PROCESSED_DIR)
    args = parser.parse_args()
    result = process_hcp_ipc(raw_dir=args.raw_dir, output_dir=args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
