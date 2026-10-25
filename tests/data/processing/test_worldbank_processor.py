import csv
import hashlib
import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.collectors.worldbank_collector import INDICATORS
from moroccan_markets_ai.data.processing.worldbank_processor import (
    OUTPUT_COLUMNS,
    WorldBankQualityError,
    process_worldbank,
)


def write_fixture(
    raw_dir: Path,
    *,
    null_year: int | None = None,
    missing_year: int | None = None,
    duplicate_year: int | None = None,
) -> None:
    raw_dir.mkdir()
    entries = []
    for code, definition in INDICATORS.items():
        records = []
        for year in (2025, 2024, 2023):
            if year == missing_year:
                continue
            record = {
                "indicator": {"id": code, "value": definition["official_name"]},
                "country": {"id": "MA", "value": "Morocco"},
                "countryiso3code": "MAR",
                "date": str(year),
                "value": None if year == null_year else float(year - 2020) / 10,
                "unit": "",
            }
            records.append(record)
            if year == duplicate_year:
                records.append(dict(record))
        payload = [
            {"page": 1, "pages": 1, "total": len(records), "sourceid": "2"},
            records,
        ]
        raw_file = raw_dir / definition["raw_filename"]
        raw_file.write_text(json.dumps(payload), encoding="utf-8")
        digest = hashlib.sha256(raw_file.read_bytes()).hexdigest()
        entries.append(
            {
                "indicator_code": code,
                "url": f"https://api.worldbank.test/{code}",
                "raw_file": str(raw_file),
                "sha256": digest,
                "api_last_updated": "2026-10-08",
            }
        )
    (raw_dir / "manifest.json").write_text(
        json.dumps(
            {
                "requested_period": {"start_year": 2023, "end_year": 2025},
                "indicators": entries,
            }
        ),
        encoding="utf-8",
    )


def test_process_normalizes_and_preserves_provenance(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    output = tmp_path / "worldbank.csv"
    quality_file = tmp_path / "quality.json"
    write_fixture(raw_dir)

    rows, quality = process_worldbank(
        raw_dir=raw_dir, output_file=output, quality_file=quality_file
    )

    assert quality["status"] == "PASSED"
    assert quality["observation_count"] == 6
    assert quality["missing_year_count"] == 0
    assert quality["null_value_count"] == 0
    assert quality["interpolation_applied"] is False
    assert "not interchangeable" in quality["semantic_warning"]
    assert rows[0]["year"] == 2023
    assert rows[0]["unit"] == "percent"
    assert len(rows[0]["source_sha256"]) == 64
    with output.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == OUTPUT_COLUMNS
        assert len(list(reader)) == 6
    assert quality_file.is_file()


def test_null_and_missing_values_are_not_invented(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    write_fixture(raw_dir, null_year=2025, missing_year=2024)

    rows, quality = process_worldbank(
        raw_dir=raw_dir,
        output_file=tmp_path / "output.csv",
        quality_file=tmp_path / "quality.json",
    )

    assert quality["status"] == "REVIEW_REQUIRED"
    assert quality["missing_year_count"] == 2
    assert quality["null_value_count"] == 2
    assert sum(row["value"] == "" for row in rows) == 2
    assert not any(row["year"] == 2024 for row in rows)


def test_duplicate_year_fails_instead_of_silent_deduplication(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    write_fixture(raw_dir, duplicate_year=2024)

    with pytest.raises(WorldBankQualityError, match="dupliquées"):
        process_worldbank(
            raw_dir=raw_dir,
            output_file=tmp_path / "output.csv",
            quality_file=tmp_path / "quality.json",
        )


def test_modified_raw_file_fails_sha_check(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    write_fixture(raw_dir)
    target = raw_dir / next(iter(INDICATORS.values()))["raw_filename"]
    target.write_text(target.read_text() + " ", encoding="utf-8")

    with pytest.raises(WorldBankQualityError, match="SHA-256"):
        process_worldbank(
            raw_dir=raw_dir,
            output_file=tmp_path / "output.csv",
            quality_file=tmp_path / "quality.json",
        )
