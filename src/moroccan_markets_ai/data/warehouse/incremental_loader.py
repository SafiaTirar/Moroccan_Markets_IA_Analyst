"""Ingestion transactionnelle de l'extension financière DATA-04.12."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from moroccan_markets_ai.data.warehouse import loader as warehouse_loader
from moroccan_markets_ai.data.warehouse.loader import (
    DEFAULT_COMPANIES,
    DEFAULT_DATABASE,
    DEFAULT_FAILED_REPORT,
    DEFAULT_HCP,
    DEFAULT_MANIFEST,
    DEFAULT_QUERY_RESULTS,
    DEFAULT_REPORT,
    DEFAULT_WORLDBANK,
    EXAMPLE_QUERIES_FILE,
    PROJECT_ROOT,
    SCHEMA_FILE,
    WarehouseInputs,
    WarehouseValidationError,
    atomic_json,
    build_warehouse,
    load_companies,
    load_financial,
    read_csv,
    sha256_file,
)

BASE_FINANCIAL = (
    PROJECT_ROOT
    / "data/quality/ammc/data_04_11/verified_financial_observations.csv"
)
BASE_REFERENCES = (
    PROJECT_ROOT / "config/quality/ammc/annual_pilot_references.json"
)
INCREMENT_FINANCIAL = (
    PROJECT_ROOT
    / "data/quality/ammc/data_04_12/verified_financial_observations.csv"
)
INCREMENT_REFERENCES = (
    PROJECT_ROOT
    / "data/quality/ammc/data_04_12/references/data_04_12_references.json"
)
OUTPUT_ROOT = PROJECT_ROOT / "data/quality/ammc/data_06_2"
COMBINED_FINANCIAL = OUTPUT_ROOT / "verified_financial_observations.csv"
COMBINED_REFERENCES = OUTPUT_ROOT / "financial_references.json"
INCREMENTAL_REPORT = OUTPUT_ROOT / "incremental_ingestion_report.json"
SCHEMA_VERSION = "1.0"


def _atomic_csv(
    path: Path, fields: list[str], rows: list[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def combine_financial_exports(
    base_path: Path, increment_path: Path, output_path: Path
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    base_fields, base_rows = read_csv(Path(base_path))
    increment_fields, increment_rows = read_csv(Path(increment_path))
    if base_fields != increment_fields:
        raise WarehouseValidationError(
            ["Schémas CSV incompatibles entre le socle et l'incrément"]
        )
    rows = [*base_rows, *increment_rows]
    non_verified = [
        row.get("verification_reference_id", "")
        for row in rows
        if row.get("verification_status") != "VERIFIED"
    ]
    if non_verified:
        raise WarehouseValidationError(
            [f"Valeurs non VERIFIED dans l'incrément : {non_verified[:3]}"]
        )
    reference_ids = [row["verification_reference_id"] for row in rows]
    duplicates = sorted(
        reference_id
        for reference_id in set(reference_ids)
        if reference_ids.count(reference_id) > 1
    )
    if duplicates:
        raise WarehouseValidationError(
            [f"Identifiants de référence dupliqués : {duplicates[:3]}"]
        )
    _atomic_csv(Path(output_path), base_fields, rows)
    return rows, {
        "base_rows": len(base_rows),
        "increment_rows": len(increment_rows),
        "combined_rows": len(rows),
        "statuses": sorted({row["verification_status"] for row in rows}),
    }


def combine_reference_files(
    base_path: Path, increment_path: Path, output_path: Path
) -> dict[str, Any]:
    base = json.loads(Path(base_path).read_text(encoding="utf-8"))
    increment = json.loads(Path(increment_path).read_text(encoding="utf-8"))
    if base.get("schema_version") != increment.get("schema_version"):
        raise WarehouseValidationError(["Versions de références incompatibles"])
    documents = [*base.get("documents", []), *increment.get("documents", [])]
    reference_ids = [
        observation.get("reference_id")
        for document in documents
        for observation in document.get("observations", [])
    ]
    missing = [item for item in reference_ids if not item]
    duplicates = sorted(
        reference_id
        for reference_id in set(reference_ids)
        if reference_id and reference_ids.count(reference_id) > 1
    )
    if missing or duplicates:
        raise WarehouseValidationError(
            [f"Références absentes ou dupliquées : {duplicates[:3]}"]
        )
    payload = {"schema_version": base["schema_version"], "documents": documents}
    atomic_json(Path(output_path), payload)
    return {
        "base_documents": len(base.get("documents", [])),
        "increment_documents": len(increment.get("documents", [])),
        "combined_documents": len(documents),
        "combined_references": len(reference_ids),
    }


def cross_report_revisions(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        key = (row[1], row[4], row[6], row[10], row[8])
        grouped[key].append(row)
    revisions = []
    for key, observations in grouped.items():
        values = {Decimal(str(row[7])) for row in observations}
        report_years = {int(row[2]) for row in observations}
        if len(values) <= 1 or len(report_years) <= 1:
            continue
        revisions.append(
            {
                "company_id": key[0],
                "observation_year": key[1],
                "indicator": key[2],
                "accounting_perimeter": key[3],
                "currency": key[4],
                "versions": [
                    {
                        "report_exercise_year": row[2],
                        "verification_reference_id": row[0],
                        "normalized_value": str(row[7]),
                    }
                    for row in sorted(observations, key=lambda item: item[2])
                ],
                "decision": "PRESERVE_BOTH_REPORT_VERSIONS",
            }
        )
    return revisions


def _fingerprint(paths: list[Path]) -> str:
    payload = {
        str(path.resolve()): sha256_file(path)
        for path in paths
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _cached_report(
    report_path: Path, database: Path, fingerprint: str
) -> dict[str, Any] | None:
    if not report_path.is_file() or not database.is_file():
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report.get("incremental_fingerprint") != fingerprint
        or report.get("database_sha256") != sha256_file(database)
    ):
        return None
    return {**report, "cache_used": True}


def run_incremental_ingestion(
    *,
    base_financial: Path = BASE_FINANCIAL,
    base_references: Path = BASE_REFERENCES,
    increment_financial: Path = INCREMENT_FINANCIAL,
    increment_references: Path = INCREMENT_REFERENCES,
    output_root: Path = OUTPUT_ROOT,
    database: Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    database = Path(database).resolve()
    report_path = output_root / INCREMENTAL_REPORT.name
    input_paths = [
        Path(base_financial),
        Path(base_references),
        Path(increment_financial),
        Path(increment_references),
        DEFAULT_COMPANIES,
        DEFAULT_MANIFEST,
        DEFAULT_HCP,
        DEFAULT_WORLDBANK,
        SCHEMA_FILE,
        EXAMPLE_QUERIES_FILE,
        Path(warehouse_loader.__file__),
        Path(__file__),
    ]
    fingerprint = _fingerprint(input_paths)
    cached = _cached_report(report_path, database, fingerprint)
    if cached is not None:
        return cached

    database_before_sha256 = sha256_file(database) if database.is_file() else None
    before_count = 0
    if database.is_file():
        connection = duckdb.connect(str(database), read_only=True)
        try:
            before_count = connection.execute(
                "SELECT count(*) FROM fact_financial_observations"
            ).fetchone()[0]
        finally:
            connection.close()

    combined_financial = output_root / COMBINED_FINANCIAL.name
    combined_references = output_root / COMBINED_REFERENCES.name
    _, financial_merge = combine_financial_exports(
        Path(base_financial), Path(increment_financial), combined_financial
    )
    reference_merge = combine_reference_files(
        Path(base_references), Path(increment_references), combined_references
    )
    companies, _ = load_companies(DEFAULT_COMPANIES)
    financial_rows, financial_audit = load_financial(
        combined_financial,
        combined_references,
        DEFAULT_MANIFEST,
        {row[0] for row in companies},
    )
    revisions = cross_report_revisions(financial_rows)
    warehouse_report = build_warehouse(
        inputs=WarehouseInputs(
            hcp=DEFAULT_HCP,
            worldbank=DEFAULT_WORLDBANK,
            financial=combined_financial,
            companies=DEFAULT_COMPANIES,
            references=combined_references,
            manifest=DEFAULT_MANIFEST,
        ),
        database=database,
        report_path=DEFAULT_REPORT,
        failed_report_path=DEFAULT_FAILED_REPORT,
        query_results_path=DEFAULT_QUERY_RESULTS,
    )
    connection = duckdb.connect(str(database), read_only=True)
    try:
        after_count = connection.execute(
            "SELECT count(*) FROM fact_financial_observations"
        ).fetchone()[0]
        non_verified = connection.execute(
            "SELECT count(*) FROM fact_financial_observations "
            "WHERE verification_status <> 'VERIFIED'"
        ).fetchone()[0]
        source_counts = {
            "financial": after_count,
            "hcp": connection.execute(
                "SELECT count(*) FROM fact_hcp_monthly"
            ).fetchone()[0],
            "worldbank": connection.execute(
                "SELECT count(*) FROM fact_worldbank_annual"
            ).fetchone()[0],
        }
    finally:
        connection.close()
    if non_verified or after_count != financial_merge["combined_rows"]:
        raise WarehouseValidationError(
            ["Contrôle post-publication de l'incrément financier échoué"]
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "COMMITTED",
        "cache_used": False,
        "incremental_fingerprint": fingerprint,
        "database": str(database),
        "database_before_sha256": database_before_sha256,
        "database_sha256": sha256_file(database),
        "financial_rows_before": financial_merge["base_rows"],
        "financial_rows_after": after_count,
        "financial_rows_added": financial_merge["increment_rows"],
        "database_rows_before_run": before_count,
        "database_rows_delta_this_run": after_count - before_count,
        "financial_merge": financial_merge,
        "reference_merge": reference_merge,
        "financial_audit": financial_audit,
        "cross_report_revisions": revisions,
        "quality": {
            "non_verified_loaded": non_verified,
            "downstream_allowed_statuses": ["VERIFIED"],
            "source_counts": source_counts,
            "quarantined_rows": 0,
        },
        "source_sha256": {
            "base_financial": sha256_file(Path(base_financial)),
            "increment_financial": sha256_file(Path(increment_financial)),
            "base_references": sha256_file(Path(base_references)),
            "increment_references": sha256_file(Path(increment_references)),
            "combined_financial": sha256_file(combined_financial),
            "combined_references": sha256_file(combined_references),
        },
        "warehouse_ingestion": warehouse_report,
    }
    atomic_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    report = run_incremental_ingestion(
        database=args.database,
        output_root=args.output_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
