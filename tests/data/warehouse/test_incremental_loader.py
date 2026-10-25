from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from moroccan_markets_ai.data.warehouse.incremental_loader import (
    combine_financial_exports,
    combine_reference_files,
    cross_report_revisions,
)
from moroccan_markets_ai.data.warehouse.loader import WarehouseValidationError

FIELDS = ["verification_reference_id", "verification_status"]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_combined_export_is_verified_and_reference_ids_are_unique(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.csv"
    increment = tmp_path / "increment.csv"
    output = tmp_path / "combined.csv"
    write_csv(base, [{"verification_reference_id": "a", "verification_status": "VERIFIED"}])
    write_csv(
        increment,
        [{"verification_reference_id": "b", "verification_status": "VERIFIED"}],
    )

    rows, audit = combine_financial_exports(base, increment, output)

    assert len(rows) == 2
    assert audit["statuses"] == ["VERIFIED"]


@pytest.mark.parametrize(
    ("increment", "message"),
    [
        (
            {"verification_reference_id": "b", "verification_status": "REVIEW_REQUIRED"},
            "non VERIFIED",
        ),
        (
            {"verification_reference_id": "a", "verification_status": "VERIFIED"},
            "dupliqués",
        ),
    ],
)
def test_combined_export_rejects_unverified_or_duplicate_references(
    tmp_path: Path, increment: dict[str, str], message: str
) -> None:
    base = tmp_path / "base.csv"
    incoming = tmp_path / "increment.csv"
    write_csv(base, [{"verification_reference_id": "a", "verification_status": "VERIFIED"}])
    write_csv(incoming, [increment])

    with pytest.raises(WarehouseValidationError, match=message):
        combine_financial_exports(base, incoming, tmp_path / "combined.csv")


def test_reference_merge_rejects_global_identifier_collision(tmp_path: Path) -> None:
    document = {
        "company_id": "issuer",
        "exercise_year": 2025,
        "observations": [{"reference_id": "same"}],
    }
    base = tmp_path / "base.json"
    increment = tmp_path / "increment.json"
    base.write_text(
        json.dumps({"schema_version": "1.0", "documents": [document]}),
        encoding="utf-8",
    )
    increment.write_text(
        json.dumps({"schema_version": "1.0", "documents": [document]}),
        encoding="utf-8",
    )

    with pytest.raises(WarehouseValidationError, match="dupliquées"):
        combine_reference_files(base, increment, tmp_path / "combined.json")


def test_cross_report_revision_preserves_both_source_versions() -> None:
    def row(reference: str, report_year: int, amount: str) -> tuple:
        return (
            reference,
            "issuer",
            report_year,
            "2024",
            2024,
            report_year != 2024,
            "NET_INCOME",
            Decimal(amount),
            "MAD",
            1,
            "CONSOLIDATED",
            "a" * 64,
            1,
            "T1",
            "VERIFIED",
        )

    revisions = cross_report_revisions(
        [row("report-2024", 2024, "100"), row("report-2025", 2025, "101")]
    )

    assert len(revisions) == 1
    assert revisions[0]["decision"] == "PRESERVE_BOTH_REPORT_VERSIONS"
    assert {item["report_exercise_year"] for item in revisions[0]["versions"]} == {
        2024,
        2025,
    }
