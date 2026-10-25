from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

from moroccan_markets_ai.data.warehouse import loader
from moroccan_markets_ai.data.warehouse.loader import (
    WarehouseInputs,
    WarehouseValidationError,
    build_warehouse,
    sha256_file,
)


@dataclass
class FixturePaths:
    inputs: WarehouseInputs
    database: Path
    report: Path
    failed_report: Path
    query_results: Path


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def warehouse_fixture(tmp_path: Path) -> FixturePaths:
    hcp_raw = tmp_path / "hcp.xlsx"
    hcp_raw.write_bytes(b"official-hcp")
    wb_raw = tmp_path / "worldbank.json"
    wb_raw.write_bytes(b'{"official": true}')
    hcp_sha = sha256_file(hcp_raw)
    wb_sha = sha256_file(wb_raw)
    pdf_sha = "a" * 64

    companies = tmp_path / "companies.json"
    companies.write_text(
        json.dumps(
            {
                "companies": [
                    {
                        "company_id": f"company_{index}",
                        "ammc_issuer": f"Issuer {index}",
                        "sector": "Test",
                        "aliases": [],
                    }
                    for index in range(15)
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.csv"
    write_csv(
        manifest,
        ["company_id", "exercise_year", "sha256"],
        [
            {
                "company_id": "company_0",
                "exercise_year": "2025",
                "sha256": pdf_sha,
            }
        ],
    )
    references = tmp_path / "references.json"
    references.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "company_id": "company_0",
                        "exercise_year": 2025,
                        "observations": [
                            {
                                "reference_id": "current",
                                "indicator": "NET_INCOME",
                                "normalized_value": "12345678901234567890.123456",
                                "currency": "MAD",
                                "scale": 1,
                                "accounting_perimeter": "CONSOLIDATED",
                                "period": "2025",
                                "page_number": 5,
                                "table_id": "T1",
                                "reliable": True,
                                "expected_status": "VERIFIED",
                            },
                            {
                                "reference_id": "comparative",
                                "indicator": "NET_INCOME",
                                "normalized_value": "99.120000",
                                "currency": "MAD",
                                "scale": 1,
                                "accounting_perimeter": "CONSOLIDATED",
                                "period": "2024",
                                "comparative_period": True,
                                "page_number": 5,
                                "table_id": "T1",
                                "reliable": True,
                                "expected_status": "VERIFIED",
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    financial = tmp_path / "financial.csv"
    financial_fields = [
        "company_id",
        "exercise_year",
        "indicator",
        "normalized_value",
        "currency",
        "scale",
        "accounting_perimeter",
        "source_pdf_sha256",
        "page_number",
        "table_id",
        "verification_reference_id",
        "verification_status",
    ]
    base = {
        "company_id": "company_0",
        "exercise_year": "2025",
        "indicator": "NET_INCOME",
        "currency": "MAD",
        "scale": "1",
        "accounting_perimeter": "CONSOLIDATED",
        "source_pdf_sha256": pdf_sha,
        "page_number": "5",
        "table_id": "T1",
        "verification_status": "VERIFIED",
    }
    write_csv(
        financial,
        financial_fields,
        [
            {
                **base,
                "normalized_value": "12345678901234567890.123456",
                "verification_reference_id": "current",
            },
            {
                **base,
                "normalized_value": "99.120000",
                "verification_reference_id": "comparative",
            },
        ],
    )
    hcp = tmp_path / "hcp.csv"
    write_csv(
        hcp,
        [
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
        ],
        [
            {
                "date": "2025-01-01",
                "indicator": "consumer_price_index",
                "category": "Indice Général",
                "value": "118.123456",
                "unit": "index_points",
                "base_year": "2017",
                "source": "https://hcp.example",
                "source_file": str(hcp_raw),
                "source_sha256": hcp_sha,
                "source_sheet": "monthly",
                "source_row": "10",
            }
        ],
    )
    worldbank = tmp_path / "worldbank.csv"
    write_csv(
        worldbank,
        [
            "country",
            "year",
            "indicator",
            "value",
            "unit",
            "source",
            "source_file",
            "source_sha256",
        ],
        [
            {
                "country": "MAR",
                "year": "2025",
                "indicator": "gdp_growth_annual_pct",
                "value": "3.123456789012345678",
                "unit": "percent",
                "source": "https://worldbank.example",
                "source_file": str(wb_raw),
                "source_sha256": wb_sha,
            }
        ],
    )
    return FixturePaths(
        inputs=WarehouseInputs(
            hcp=hcp,
            worldbank=worldbank,
            financial=financial,
            companies=companies,
            references=references,
            manifest=manifest,
        ),
        database=tmp_path / "warehouse.duckdb",
        report=tmp_path / "report.json",
        failed_report=tmp_path / "failed.json",
        query_results=tmp_path / "queries.json",
    )


def build(paths: FixturePaths) -> dict:
    return build_warehouse(
        inputs=paths.inputs,
        database=paths.database,
        report_path=paths.report,
        failed_report_path=paths.failed_report,
        query_results_path=paths.query_results,
    )


def test_transactional_load_preserves_decimal_units_scope_and_periods(
    warehouse_fixture: FixturePaths,
) -> None:
    report = build(warehouse_fixture)
    connection = duckdb.connect(str(warehouse_fixture.database), read_only=True)

    assert report["loaded_rows"] == {
        "dim_company": 15,
        "fact_financial_observations": 2,
        "fact_hcp_monthly": 1,
        "fact_worldbank_annual": 1,
    }
    financial = connection.execute(
        "SELECT observation_year, is_comparative, normalized_value::VARCHAR, "
        "currency, accounting_perimeter FROM fact_financial_observations "
        "ORDER BY observation_year DESC"
    ).fetchall()
    assert financial == [
        (2025, False, "12345678901234567890.123456", "MAD", "CONSOLIDATED"),
        (2024, True, "99.120000", "MAD", "CONSOLIDATED"),
    ]
    assert connection.execute(
        "SELECT value::VARCHAR, unit FROM fact_hcp_monthly"
    ).fetchone() == ("118.123456", "index_points")
    assert connection.execute(
        "SELECT value::VARCHAR, unit FROM fact_worldbank_annual"
    ).fetchone() == ("3.123456789012345678", "percent")
    assert connection.execute(
        "SELECT count(*) FROM fact_financial_observations "
        "WHERE verification_status <> 'VERIFIED'"
    ).fetchone()[0] == 0
    connection.close()


def test_second_identical_load_uses_cache_without_modifying_database(
    warehouse_fixture: FixturePaths,
) -> None:
    first = build(warehouse_fixture)
    before_hash = sha256_file(warehouse_fixture.database)
    before_mtime = warehouse_fixture.database.stat().st_mtime_ns

    second = build(warehouse_fixture)

    assert not first["cache_used"]
    assert second["cache_used"]
    assert sha256_file(warehouse_fixture.database) == before_hash
    assert warehouse_fixture.database.stat().st_mtime_ns == before_mtime


def test_different_reports_can_preserve_revised_value_for_same_period(
    warehouse_fixture: FixturePaths,
) -> None:
    reference_payload = json.loads(
        warehouse_fixture.inputs.references.read_text(encoding="utf-8")
    )
    reference_payload["documents"].append(
        {
            "company_id": "company_0",
            "exercise_year": 2024,
            "observations": [
                {
                    "reference_id": "original-2024",
                    "indicator": "NET_INCOME",
                    "normalized_value": "98.000000",
                    "currency": "MAD",
                    "scale": 1,
                    "accounting_perimeter": "CONSOLIDATED",
                    "period": "2024",
                    "page_number": 4,
                    "table_id": "T0",
                    "reliable": True,
                    "expected_status": "VERIFIED",
                }
            ],
        }
    )
    warehouse_fixture.inputs.references.write_text(
        json.dumps(reference_payload), encoding="utf-8"
    )
    manifest_fields, manifest_rows = loader.read_csv(
        warehouse_fixture.inputs.manifest
    )
    write_csv(
        warehouse_fixture.inputs.manifest,
        manifest_fields,
        [
            *manifest_rows,
            {
                "company_id": "company_0",
                "exercise_year": "2024",
                "sha256": "a" * 64,
            },
        ],
    )
    financial_fields, financial_rows = loader.read_csv(
        warehouse_fixture.inputs.financial
    )
    write_csv(
        warehouse_fixture.inputs.financial,
        financial_fields,
        [
            *financial_rows,
            {
                **financial_rows[0],
                "exercise_year": "2024",
                "normalized_value": "98.000000",
                "page_number": "4",
                "table_id": "T0",
                "verification_reference_id": "original-2024",
            },
        ],
    )

    report = build(warehouse_fixture)
    connection = duckdb.connect(str(warehouse_fixture.database), read_only=True)
    versions = connection.execute(
        "SELECT report_exercise_year, normalized_value::VARCHAR "
        "FROM fact_financial_observations WHERE observation_year = 2024 "
        "ORDER BY report_exercise_year"
    ).fetchall()
    connection.close()

    assert report["loaded_rows"]["fact_financial_observations"] == 3
    assert versions == [(2024, "98.000000"), (2025, "99.120000")]


@pytest.mark.parametrize(
    ("dataset", "field", "value", "message"),
    [
        ("financial", "verification_status", "REVIEW_REQUIRED", "non VERIFIED"),
        ("financial", "currency", "EUR", "Devise"),
        ("hcp", "unit", "percent", "indice mensuel"),
        ("worldbank", "unit", "index_points", "World Bank"),
    ],
)
def test_invalid_status_or_unit_is_rejected_and_published_database_is_preserved(
    warehouse_fixture: FixturePaths,
    dataset: str,
    field: str,
    value: str,
    message: str,
) -> None:
    build(warehouse_fixture)
    database_hash = sha256_file(warehouse_fixture.database)
    path = getattr(warehouse_fixture.inputs, dataset)
    fields, rows = loader.read_csv(path)
    rows[0][field] = value
    write_csv(path, fields, rows)

    with pytest.raises(WarehouseValidationError, match=message):
        build(warehouse_fixture)

    assert sha256_file(warehouse_fixture.database) == database_hash
    assert json.loads(warehouse_fixture.failed_report.read_text())["database_preserved"]


def test_exact_duplicate_is_rejected(warehouse_fixture: FixturePaths) -> None:
    fields, rows = loader.read_csv(warehouse_fixture.inputs.hcp)
    write_csv(warehouse_fixture.inputs.hcp, fields, [*rows, rows[0]])

    with pytest.raises(WarehouseValidationError, match="Doublon exact"):
        build(warehouse_fixture)

    assert not warehouse_fixture.database.exists()


def test_database_transaction_rolls_back_on_schema_error(
    warehouse_fixture: FixturePaths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build(warehouse_fixture)
    database_hash = sha256_file(warehouse_fixture.database)
    invalid_schema = tmp_path / "invalid.sql"
    invalid_schema.write_text(
        "CREATE TABLE transient_table(id INTEGER); INVALID SQL;",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "SCHEMA_FILE", invalid_schema)

    with pytest.raises(duckdb.Error):
        build(warehouse_fixture)

    assert sha256_file(warehouse_fixture.database) == database_hash
    connection = duckdb.connect(str(warehouse_fixture.database), read_only=True)
    assert connection.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'transient_table'"
    ).fetchone()[0] == 0
    connection.close()
