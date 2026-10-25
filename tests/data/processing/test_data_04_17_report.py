import csv
from pathlib import Path

from moroccan_markets_ai.data.processing.data_04_17_report import (
    VERIFIED_COLUMNS,
    _empty_verified_csv,
)


def test_empty_verified_export_is_readable_and_cannot_leak_values(tmp_path: Path) -> None:
    output = tmp_path / "verified.csv"
    _empty_verified_csv(output)

    with output.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == VERIFIED_COLUMNS
        assert list(reader) == []
