import csv
import hashlib
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from moroccan_markets_ai.data.processing.hcp_processor import (
    HCPQualityError,
    extract_observations,
    inspect_workbook,
    process_hcp_ipc,
)


def create_workbook(
    path: Path,
    *,
    periods: tuple[str, ...] = ("2023/02", "2023/01"),
    missing: bool = False,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "mensuel (Base 100  2017)"
    sheet.append([])
    sheet.append([None, "Mois", "Alimentation", "Produits Non Alimentaires", "Indice Général"])
    for index, period in enumerate(periods):
        sheet.append(
            [
                None,
                period,
                None if missing and index == 0 else 110.1 + index,
                105.2 + index,
                107.0 + index,
            ]
        )
    source_row = sheet.max_row + 1
    sheet.cell(source_row, 2, "Source : Haut Commissariat au Plan")
    sheet.merge_cells(start_row=source_row, start_column=2, end_row=source_row, end_column=5)
    workbook.save(path)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inspection_reports_real_structure(tmp_path: Path) -> None:
    workbook = tmp_path / "ipc.xlsx"
    create_workbook(workbook)

    structure = inspect_workbook(workbook)

    sheet = structure["sheets"][0]
    assert structure["sheet_count"] == 1
    assert sheet["header_row"] == 2
    assert sheet["data_row_count"] == 2
    assert sheet["merged_cells"] == ["B5:E5"]
    assert sheet["source_notes"] == ["Source : Haut Commissariat au Plan"]


def test_extract_normalizes_index_without_calling_it_inflation(tmp_path: Path) -> None:
    workbook = tmp_path / "ipc.xlsx"
    create_workbook(workbook)

    rows, quality = extract_observations(workbook, source_sha256=file_sha(workbook))

    assert len(rows) == 6
    assert rows[0]["date"] == "2023-01-01"
    assert rows[0]["indicator"] == "consumer_price_index"
    assert rows[0]["unit"] == "index_points"
    assert rows[0]["base_year"] == 2017
    assert quality["missing_value_count"] == 0
    assert quality["missing_months"] == []
    assert "n'est pas un taux d'inflation" in quality["indicator_semantics"]


def test_missing_value_is_preserved_and_reported(tmp_path: Path) -> None:
    workbook = tmp_path / "ipc.xlsx"
    create_workbook(workbook, missing=True)

    rows, quality = extract_observations(workbook, source_sha256=file_sha(workbook))

    missing = [row for row in rows if row["value"] == ""]
    assert len(missing) == 1
    assert missing[0]["date"] == "2023-02-01"
    assert quality["missing_value_count"] == 1


def test_duplicate_month_fails_instead_of_silent_deduplication(tmp_path: Path) -> None:
    workbook = tmp_path / "ipc.xlsx"
    create_workbook(workbook, periods=("2023/01", "2023/01"))

    with pytest.raises(HCPQualityError, match="dupliquées"):
        extract_observations(workbook, source_sha256=file_sha(workbook))


def test_process_writes_dataset_structure_and_quality_report(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "processed"
    raw_dir.mkdir()
    workbook = raw_dir / "hcp_ipc_base_2017_monthly.xlsx"
    create_workbook(workbook)
    digest = file_sha(workbook)
    (raw_dir / "metadata.json").write_text(
        json.dumps({"sha256": digest}),
        encoding="utf-8",
    )

    report = process_hcp_ipc(raw_dir=raw_dir, output_dir=output_dir)

    assert report["status"] == "VALID"
    with (output_dir / "hcp_ipc_monthly.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6
    assert {row["source_sha256"] for row in rows} == {digest}
    assert (output_dir / "hcp_ipc_workbook_structure.json").is_file()
    assert (output_dir / "hcp_ipc_quality_report.json").is_file()
