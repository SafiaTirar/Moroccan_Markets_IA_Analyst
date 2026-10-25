"""Interface analytique en lecture seule pour les versions financières."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb

from moroccan_markets_ai.data.warehouse.loader import (
    DEFAULT_DATABASE,
    PROJECT_ROOT,
    atomic_json,
    sha256_file,
)

DEFAULT_REPORT = (
    PROJECT_ROOT / "data/quality/ammc/data_06_3/version_comparison_report.json"
)


class FinancialVersionPolicy(StrEnum):
    AS_REPORTED = "AS_REPORTED"
    LATEST_AVAILABLE = "LATEST_AVAILABLE"
    ALL_VERSIONS = "ALL_VERSIONS"


POLICY_VIEWS = {
    FinancialVersionPolicy.AS_REPORTED: "v_financial_as_reported",
    FinancialVersionPolicy.LATEST_AVAILABLE: "v_financial_latest_available",
    FinancialVersionPolicy.ALL_VERSIONS: "v_financial_all_versions",
}


def resolve_priority(
    rows: list[dict[str, Any]], policy: FinancialVersionPolicy
) -> dict[str, Any]:
    """Résout une priorité sans sélectionner arbitrairement un conflit."""
    if policy is FinancialVersionPolicy.ALL_VERSIONS:
        return {"status": "AVAILABLE", "rows": rows}
    if not rows:
        return {"status": "MISSING", "rows": []}
    if policy is FinancialVersionPolicy.AS_REPORTED:
        candidates = [
            row
            for row in rows
            if row["report_exercise_year"] == row["observation_year"]
        ]
        missing_status = "MISSING_AS_REPORTED"
    else:
        latest_year = max(row["report_exercise_year"] for row in rows)
        candidates = [
            row for row in rows if row["report_exercise_year"] == latest_year
        ]
        missing_status = "MISSING"
    if not candidates:
        return {"status": missing_status, "rows": []}
    if len(candidates) > 1:
        return {"status": "CONFLICTING_PRIORITY", "rows": candidates}
    return {"status": "AVAILABLE", "rows": candidates}


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def query_financial(
    database: Path = DEFAULT_DATABASE,
    *,
    policy: FinancialVersionPolicy | str = FinancialVersionPolicy.AS_REPORTED,
    company_id: str | None = None,
    indicator: str | None = None,
    accounting_perimeter: str | None = None,
    observation_year: int | None = None,
) -> list[dict[str, Any]]:
    selected_policy = FinancialVersionPolicy(policy)
    view = POLICY_VIEWS[selected_policy]
    clauses = []
    parameters: list[Any] = []
    for column, value in (
        ("company_id", company_id),
        ("indicator", indicator),
        ("accounting_perimeter", accounting_perimeter),
        ("observation_year", observation_year),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT * FROM {view}{where} "
        "ORDER BY company_id, observation_year, indicator, "
        "accounting_perimeter, selected_report_exercise_year NULLS LAST"
    )
    connection = duckdb.connect(str(Path(database).resolve()), read_only=True)
    try:
        return _rows(connection.execute(sql, parameters))
    finally:
        connection.close()


def calculate_growth(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
) -> dict[str, Any]:
    if first is None or second is None:
        return {
            "status": "NOT_COMPARABLE",
            "reason": "MISSING_OBSERVATION",
            "absolute_change": None,
            "growth_pct": None,
        }
    comparable_fields = (
        "company_id",
        "indicator",
        "accounting_perimeter",
        "currency",
    )
    mismatches = [
        field for field in comparable_fields if first.get(field) != second.get(field)
    ]
    if mismatches:
        return {
            "status": "NOT_COMPARABLE",
            "reason": "INCOMPATIBLE_" + "_".join(mismatches).upper(),
            "absolute_change": None,
            "growth_pct": None,
        }
    if first.get("selection_status") != "AVAILABLE" or second.get(
        "selection_status"
    ) != "AVAILABLE":
        return {
            "status": "NOT_COMPARABLE",
            "reason": "UNAVAILABLE_SELECTED_VERSION",
            "absolute_change": None,
            "growth_pct": None,
        }
    first_value = first.get("normalized_value")
    second_value = second.get("normalized_value")
    if first_value is None or second_value is None:
        return {
            "status": "NOT_COMPARABLE",
            "reason": "MISSING_VALUE",
            "absolute_change": None,
            "growth_pct": None,
        }
    first_decimal = Decimal(str(first_value))
    second_decimal = Decimal(str(second_value))
    absolute_change = second_decimal - first_decimal
    if first_decimal == 0:
        return {
            "status": "NOT_COMPARABLE",
            "reason": "ZERO_BASE_VALUE",
            "absolute_change": str(absolute_change),
            "growth_pct": None,
        }
    return {
        "status": "AVAILABLE",
        "reason": None,
        "absolute_change": str(absolute_change),
        "growth_pct": str((absolute_change / abs(first_decimal)) * 100),
    }


def growth_between_years(
    database: Path,
    *,
    policy: FinancialVersionPolicy | str,
    company_id: str,
    indicator: str,
    accounting_perimeter: str,
    first_year: int,
    second_year: int,
) -> dict[str, Any]:
    selected_policy = FinancialVersionPolicy(policy)
    if selected_policy is FinancialVersionPolicy.ALL_VERSIONS:
        return {
            "status": "NOT_COMPARABLE",
            "reason": "ALL_VERSIONS_REQUIRES_EXPLICIT_VERSION_SELECTION",
            "absolute_change": None,
            "growth_pct": None,
            "selection_policy": selected_policy.value,
        }
    observations = query_financial(
        database,
        policy=selected_policy,
        company_id=company_id,
        indicator=indicator,
        accounting_perimeter=accounting_perimeter,
    )
    by_year = {int(row["observation_year"]): row for row in observations}
    result = calculate_growth(by_year.get(first_year), by_year.get(second_year))
    return {
        **result,
        "selection_policy": selected_policy.value,
        "first_year": first_year,
        "second_year": second_year,
    }


def build_version_report(
    database: Path = DEFAULT_DATABASE, report_path: Path = DEFAULT_REPORT
) -> dict[str, Any]:
    database = Path(database).resolve()
    connection = duckdb.connect(str(database), read_only=True)
    try:
        divergences = _rows(
            connection.execute(
                "SELECT * FROM v_financial_version_comparison "
                "WHERE comparison_status = 'DIVERGENT' "
                "ORDER BY company_id, observation_year, indicator"
            )
        )
        policy_counts = {
            policy.value: dict(
                connection.execute(
                    f"SELECT selection_status, count(*) FROM {view} GROUP BY 1"
                ).fetchall()
            )
            for policy, view in POLICY_VIEWS.items()
        }
        s2m_examples = {
            policy.value: query_financial(
                database,
                policy=policy,
                company_id="s2m",
                observation_year=2024,
                accounting_perimeter="CONSOLIDATED",
            )
            for policy in FinancialVersionPolicy
        }
    finally:
        connection.close()
    report = {
        "schema_version": "1.0",
        "database": str(database),
        "database_sha256": sha256_file(database),
        "read_only_queries": True,
        "policies": {
            "AS_REPORTED": (
                "Valeur du rapport de l'exercice observé; aucun fallback."
            ),
            "LATEST_AVAILABLE": (
                "Valeur du rapport le plus récent; elle n'est pas qualifiée "
                "automatiquement de corrigée ou retraitée."
            ),
            "ALL_VERSIONS": "Toutes les versions VERIFIED avec leur provenance.",
        },
        "policy_status_counts": policy_counts,
        "divergence_count": len(divergences),
        "divergences": divergences,
        "s2m_2024_examples": s2m_examples,
        "growth_rule": (
            "Croissance autorisée uniquement pour même entreprise, indicateur, "
            "périmètre et devise, avec deux versions disponibles explicitement."
        ),
    }
    atomic_json(Path(report_path), report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = build_version_report(args.database, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
