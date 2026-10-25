from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from moroccan_markets_ai.data.warehouse.financial_analytics import (
    FinancialVersionPolicy,
    calculate_growth,
    growth_between_years,
    query_financial,
    resolve_priority,
)
from moroccan_markets_ai.data.warehouse.loader import (
    DEFAULT_DATABASE,
    SCHEMA_FILE,
    sha256_file,
)


def financial_row(
    reference: str,
    *,
    report_year: int,
    observation_year: int,
    indicator: str,
    amount: str,
    perimeter: str = "CONSOLIDATED",
) -> tuple:
    return (
        reference,
        "issuer",
        report_year,
        str(observation_year),
        observation_year,
        report_year != observation_year,
        indicator,
        amount,
        "MAD",
        1,
        perimeter,
        "a" * 64,
        5,
        "T1",
        "VERIFIED",
    )


@pytest.fixture
def versioned_database(tmp_path: Path) -> Path:
    database = tmp_path / "versions.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO dim_company VALUES ('issuer', 'Issuer', 'Industrial', '[]')"
    )
    rows = [
        financial_row(
            "net-2023", report_year=2023, observation_year=2023,
            indicator="NET_INCOME", amount="80",
        ),
        financial_row(
            "net-original", report_year=2024, observation_year=2024,
            indicator="NET_INCOME", amount="100",
        ),
        financial_row(
            "net-latest", report_year=2025, observation_year=2024,
            indicator="NET_INCOME", amount="101",
        ),
        financial_row(
            "revenue-latest", report_year=2025, observation_year=2024,
            indicator="REVENUE", amount="200",
        ),
        financial_row(
            "group-share", report_year=2024, observation_year=2024,
            indicator="NET_INCOME_GROUP_SHARE", amount="90",
        ),
        financial_row(
            "social-net", report_year=2024, observation_year=2024,
            indicator="NET_INCOME", amount="50", perimeter="SOCIAL",
        ),
    ]
    connection.executemany(
        "INSERT INTO fact_financial_observations VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.close()
    return database


def test_as_reported_has_no_silent_fallback(versioned_database: Path) -> None:
    before = sha256_file(versioned_database)
    rows = query_financial(
        versioned_database,
        policy="AS_REPORTED",
        company_id="issuer",
        observation_year=2024,
        accounting_perimeter="CONSOLIDATED",
    )
    by_indicator = {row["indicator"]: row for row in rows}

    assert str(by_indicator["NET_INCOME"]["normalized_value"]) == "100.000000"
    assert by_indicator["NET_INCOME"]["selected_report_exercise_year"] == 2024
    assert by_indicator["REVENUE"]["selection_status"] == "MISSING_AS_REPORTED"
    assert by_indicator["REVENUE"]["normalized_value"] is None
    assert sha256_file(versioned_database) == before


def test_latest_available_and_all_versions_preserve_originals(
    versioned_database: Path,
) -> None:
    latest = query_financial(
        versioned_database,
        policy=FinancialVersionPolicy.LATEST_AVAILABLE,
        company_id="issuer",
        indicator="NET_INCOME",
        accounting_perimeter="CONSOLIDATED",
        observation_year=2024,
    )
    versions = query_financial(
        versioned_database,
        policy=FinancialVersionPolicy.ALL_VERSIONS,
        company_id="issuer",
        indicator="NET_INCOME",
        accounting_perimeter="CONSOLIDATED",
        observation_year=2024,
    )

    assert str(latest[0]["normalized_value"]) == "101.000000"
    assert latest[0]["selected_report_exercise_year"] == 2025
    assert [str(row["normalized_value"]) for row in versions] == [
        "100.000000",
        "101.000000",
    ]


def test_indicators_and_perimeters_remain_separate(versioned_database: Path) -> None:
    consolidated = query_financial(
        versioned_database,
        policy="AS_REPORTED",
        company_id="issuer",
        indicator="NET_INCOME",
        accounting_perimeter="CONSOLIDATED",
        observation_year=2024,
    )
    social = query_financial(
        versioned_database,
        policy="AS_REPORTED",
        company_id="issuer",
        indicator="NET_INCOME",
        accounting_perimeter="SOCIAL",
        observation_year=2024,
    )
    group_share = query_financial(
        versioned_database,
        policy="AS_REPORTED",
        company_id="issuer",
        indicator="NET_INCOME_GROUP_SHARE",
        accounting_perimeter="CONSOLIDATED",
        observation_year=2024,
    )

    assert str(consolidated[0]["normalized_value"]) == "100.000000"
    assert str(social[0]["normalized_value"]) == "50.000000"
    assert str(group_share[0]["normalized_value"]) == "90.000000"


def test_conflicting_same_priority_is_not_selected() -> None:
    rows = [
        {"report_exercise_year": 2025, "observation_year": 2024, "value": 1},
        {"report_exercise_year": 2025, "observation_year": 2024, "value": 2},
    ]

    result = resolve_priority(rows, FinancialVersionPolicy.LATEST_AVAILABLE)

    assert result["status"] == "CONFLICTING_PRIORITY"
    assert len(result["rows"]) == 2


def test_growth_requires_compatible_available_values(
    versioned_database: Path,
) -> None:
    result = growth_between_years(
        versioned_database,
        policy="AS_REPORTED",
        company_id="issuer",
        indicator="NET_INCOME",
        accounting_perimeter="CONSOLIDATED",
        first_year=2023,
        second_year=2024,
    )
    incompatible = calculate_growth(
        {
            "company_id": "issuer", "indicator": "NET_INCOME",
            "accounting_perimeter": "SOCIAL", "currency": "MAD",
            "selection_status": "AVAILABLE", "normalized_value": 1,
        },
        {
            "company_id": "issuer", "indicator": "NET_INCOME",
            "accounting_perimeter": "CONSOLIDATED", "currency": "MAD",
            "selection_status": "AVAILABLE", "normalized_value": 2,
        },
    )

    assert result["status"] == "AVAILABLE"
    assert result["growth_pct"] == "25.00"
    assert incompatible["status"] == "NOT_COMPARABLE"
    assert "ACCOUNTING_PERIMETER" in incompatible["reason"]


@pytest.mark.skipif(not DEFAULT_DATABASE.is_file(), reason="Warehouse local absent")
def test_real_s2m_2024_divergences_are_preserved() -> None:
    connection = duckdb.connect(str(DEFAULT_DATABASE), read_only=True)
    rows = connection.execute(
        "SELECT indicator, first_value::VARCHAR, latest_value::VARCHAR "
        "FROM v_financial_version_comparison WHERE company_id = 's2m' "
        "AND observation_year = 2024 AND comparison_status = 'DIVERGENT' "
        "ORDER BY indicator"
    ).fetchall()
    connection.close()

    assert rows == [
        ("NET_INCOME", "28463643.000000", "28464006.000000"),
        ("NET_INCOME_GROUP_SHARE", "29413480.000000", "29413844.000000"),
    ]
