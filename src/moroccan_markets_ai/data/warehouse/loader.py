"""Construction transactionnelle du Data Warehouse analytique DuckDB."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[4]
WAREHOUSE_DIR = PROJECT_ROOT / "data" / "warehouse"
DEFAULT_DATABASE = WAREHOUSE_DIR / "moroccan_markets.duckdb"
DEFAULT_REPORT = WAREHOUSE_DIR / "ingestion_report.json"
DEFAULT_FAILED_REPORT = WAREHOUSE_DIR / "last_failed_ingestion.json"
DEFAULT_QUERY_RESULTS = WAREHOUSE_DIR / "example_query_results.json"
DEFAULT_HCP = PROJECT_ROOT / "data/processed/macroeconomic/hcp_ipc_monthly.csv"
DEFAULT_WORLDBANK = (
    PROJECT_ROOT / "data/processed/macroeconomic/worldbank_morocco_annual.csv"
)
DEFAULT_FINANCIAL = (
    PROJECT_ROOT
    / "data/quality/ammc/data_04_11/verified_financial_observations.csv"
)
DEFAULT_COMPANIES = PROJECT_ROOT / "config/data/ammc_corpus.json"
DEFAULT_REFERENCES = (
    PROJECT_ROOT / "config/quality/ammc/annual_pilot_references.json"
)
DEFAULT_MANIFEST = PROJECT_ROOT / "data/raw/ammc/corpus/download_manifest.csv"
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
EXAMPLE_QUERIES_FILE = Path(__file__).with_name("example_queries.sql")
WAREHOUSE_SCHEMA_VERSION = "1.0"
DUCKDB_MEMORY_LIMIT = "1GB"
DUCKDB_THREADS = 1
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class WarehouseValidationError(ValueError):
    """Erreur critique interdisant la publication de la base."""

    def __init__(self, issues: Iterable[str]) -> None:
        self.issues = list(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class WarehouseInputs:
    hcp: Path = DEFAULT_HCP
    worldbank: Path = DEFAULT_WORLDBANK
    financial: Path = DEFAULT_FINANCIAL
    companies: Path = DEFAULT_COMPANIES
    references: Path = DEFAULT_REFERENCES
    manifest: Path = DEFAULT_MANIFEST

    def resolved(self) -> WarehouseInputs:
        return WarehouseInputs(
            **{
                name: Path(value).resolve()
                for name, value in self.__dict__.items()
            }
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def require_columns(
    path: Path, columns: list[str], required: set[str]
) -> None:
    missing = sorted(required - set(columns))
    if missing:
        raise WarehouseValidationError(
            [f"Colonnes absentes dans {path.name}: {', '.join(missing)}"]
        )


def parse_decimal(value: str, field: str, *, nullable: bool = False) -> Decimal | None:
    if not value.strip():
        if nullable:
            return None
        raise WarehouseValidationError([f"Valeur obligatoire absente : {field}"])
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise WarehouseValidationError([f"Valeur décimale invalide : {field}={value}"]) from exc
    if not parsed.is_finite():
        raise WarehouseValidationError([f"Valeur non finie : {field}={value}"])
    return parsed


def parse_int(value: str, field: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise WarehouseValidationError([f"Entier invalide : {field}={value}"]) from exc


def validate_sha256(value: str, field: str) -> None:
    if not SHA256_RE.fullmatch(value):
        raise WarehouseValidationError([f"SHA-256 invalide : {field}"])


def validate_provenance_files(rows: list[dict[str, str]], dataset: str) -> None:
    expected_by_path: dict[Path, str] = {}
    for row in rows:
        source_path = Path(row["source_file"])
        expected = row["source_sha256"]
        validate_sha256(expected, f"{dataset}.source_sha256")
        previous = expected_by_path.setdefault(source_path, expected)
        if previous != expected:
            raise WarehouseValidationError(
                [f"Hashes contradictoires pour la provenance {source_path}"]
            )
    for source_path, expected in expected_by_path.items():
        if not source_path.is_file():
            raise WarehouseValidationError(
                [f"Fichier de provenance absent : {source_path}"]
            )
        if sha256_file(source_path) != expected:
            raise WarehouseValidationError(
                [f"Fichier de provenance modifié : {source_path}"]
            )


def reject_duplicate_rows(
    rows: list[dict[str, str]], *, dataset: str, key_fields: tuple[str, ...]
) -> None:
    exact = Counter(tuple(sorted(row.items())) for row in rows)
    if any(count > 1 for count in exact.values()):
        raise WarehouseValidationError([f"Doublon exact détecté dans {dataset}"])
    keys = Counter(tuple(row[field] for field in key_fields) for row in rows)
    duplicates = [key for key, count in keys.items() if count > 1]
    if duplicates:
        raise WarehouseValidationError(
            [f"Conflit de clé métier dans {dataset}: {duplicates[:3]}"]
        )


def load_companies(path: Path) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    companies = payload.get("companies", [])
    ids = [str(item.get("company_id", "")) for item in companies]
    if len(companies) != 15 or len(ids) != len(set(ids)) or not all(ids):
        raise WarehouseValidationError(
            ["Le référentiel doit contenir exactement 15 company_id uniques"]
        )
    rows = [
        (
            item["company_id"],
            item["ammc_issuer"],
            item["sector"],
            json.dumps(item.get("aliases", []), ensure_ascii=False),
        )
        for item in companies
    ]
    return rows, {"row_count": len(rows), "company_ids": sorted(ids)}


def reference_index(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    references: dict[str, dict[str, Any]] = {}
    for document in payload.get("documents", []):
        for observation in document.get("observations", []):
            reference_id = observation.get("reference_id")
            if not reference_id or reference_id in references:
                raise WarehouseValidationError(
                    [f"Référence financière absente ou dupliquée : {reference_id}"]
                )
            references[reference_id] = {
                **observation,
                "company_id": document["company_id"],
                "exercise_year": int(document["exercise_year"]),
            }
    return references


def manifest_hashes(path: Path) -> set[tuple[str, int, str]]:
    _, rows = read_csv(path)
    result = set()
    for row in rows:
        if row.get("sha256"):
            result.add(
                (row["company_id"], int(row["exercise_year"]), row["sha256"])
            )
    return result


def load_financial(
    path: Path, references_path: Path, manifest_path: Path, company_ids: set[str]
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    columns, source_rows = read_csv(path)
    required = {
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
    }
    require_columns(path, columns, required)
    reject_duplicate_rows(
        source_rows,
        dataset="AMMC",
        key_fields=("verification_reference_id",),
    )
    references = reference_index(references_path)
    available_pdfs = manifest_hashes(manifest_path)
    rows: list[tuple[Any, ...]] = []
    business_values: dict[tuple[Any, ...], Decimal] = {}
    seen_business_keys: set[tuple[Any, ...]] = set()
    comparative_count = 0
    for source in source_rows:
        status = source["verification_status"]
        if status != "VERIFIED":
            raise WarehouseValidationError(
                [f"Observation AMMC non VERIFIED : {source['verification_reference_id']}"]
            )
        reference_id = source["verification_reference_id"]
        reference = references.get(reference_id)
        if (
            reference is None
            or reference.get("expected_status") != "VERIFIED"
            or reference.get("reliable") is not True
        ):
            raise WarehouseValidationError(
                [f"Référence VERIFIED fiable absente : {reference_id}"]
            )
        company_id = source["company_id"]
        exercise_year = parse_int(source["exercise_year"], "exercise_year")
        if company_id not in company_ids:
            raise WarehouseValidationError([f"Entreprise AMMC inconnue : {company_id}"])
        if (
            reference["company_id"] != company_id
            or reference["exercise_year"] != exercise_year
        ):
            raise WarehouseValidationError(
                [f"Identité ou exercice incompatible pour {reference_id}"]
            )
        period = str(reference.get("period", ""))
        if not re.fullmatch(r"20\d{2}", period):
            raise WarehouseValidationError([f"Période invalide pour {reference_id}"])
        observation_year = int(period)
        is_comparative = bool(reference.get("comparative_period", False))
        if is_comparative != (observation_year != exercise_year):
            raise WarehouseValidationError(
                [f"Marqueur comparatif incohérent pour {reference_id}"]
            )
        if observation_year > exercise_year:
            raise WarehouseValidationError(
                [f"Période future interdite pour {reference_id}"]
            )
        amount = parse_decimal(source["normalized_value"], "normalized_value")
        assert amount is not None
        reference_amount = parse_decimal(
            str(reference.get("normalized_value", "")),
            "reference.normalized_value",
        )
        if reference_amount != amount or reference.get("indicator") != source["indicator"]:
            raise WarehouseValidationError(
                [f"Montant ou indicateur divergent de la référence : {reference_id}"]
            )
        currency = source["currency"]
        if currency != "MAD":
            raise WarehouseValidationError([f"Devise AMMC incohérente : {currency}"])
        scale = parse_int(source["scale"], "scale")
        if scale <= 0:
            raise WarehouseValidationError([f"Échelle invalide pour {reference_id}"])
        if reference.get("currency") != currency or int(reference.get("scale", 0)) != scale:
            raise WarehouseValidationError(
                [f"Devise ou échelle divergente de la référence : {reference_id}"]
            )
        perimeter = source["accounting_perimeter"]
        if perimeter not in {"SOCIAL", "CONSOLIDATED"}:
            raise WarehouseValidationError(
                [f"Périmètre invalide pour {reference_id}: {perimeter}"]
            )
        if reference.get("accounting_perimeter") != perimeter:
            raise WarehouseValidationError(
                [f"Périmètre divergent de la référence : {reference_id}"]
            )
        sha256 = source["source_pdf_sha256"]
        validate_sha256(sha256, "source_pdf_sha256")
        if (company_id, exercise_year, sha256) not in available_pdfs:
            raise WarehouseValidationError(
                [f"PDF absent du manifeste pour {reference_id}"]
            )
        if not source["table_id"] or not source["page_number"]:
            raise WarehouseValidationError(
                [f"Provenance page/table absente pour {reference_id}"]
            )
        page_number = parse_int(source["page_number"], "page_number")
        if page_number <= 0:
            raise WarehouseValidationError([f"Page invalide pour {reference_id}"])
        if (
            int(reference.get("page_number", 0)) != page_number
            or reference.get("table_id") != source["table_id"]
        ):
            raise WarehouseValidationError(
                [f"Page ou table divergente de la référence : {reference_id}"]
            )
        key = (
            company_id,
            exercise_year,
            observation_year,
            source["indicator"],
            perimeter,
            currency,
        )
        previous = business_values.get(key)
        if previous is not None and previous != amount:
            raise WarehouseValidationError(
                [f"Valeurs contradictoires pour la clé AMMC {key}"]
            )
        if key in seen_business_keys:
            raise WarehouseValidationError([f"Doublon de clé métier AMMC : {key}"])
        business_values[key] = amount
        seen_business_keys.add(key)
        comparative_count += int(is_comparative)
        rows.append(
            (
                reference_id,
                company_id,
                exercise_year,
                period,
                observation_year,
                is_comparative,
                source["indicator"],
                amount,
                currency,
                scale,
                perimeter,
                sha256,
                page_number,
                source["table_id"],
                status,
            )
        )
    report_keys = {
        (row[1], row[2], row[6], row[10])
        for row in rows
    }
    return rows, {
        "columns": columns,
        "row_count": len(rows),
        "missing_value_count": sum(
            not value.strip() for row in source_rows for value in row.values()
        ),
        "verification_statuses": dict(Counter(row[-1] for row in rows)),
        "comparative_observation_count": comparative_count,
        "current_observation_count": len(rows) - comparative_count,
        "report_level_combination_count": len(report_keys),
        "observation_level_key_count": len(rows),
        "quarantined_rows": 0,
    }


def load_hcp(path: Path) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    columns, source_rows = read_csv(path)
    required = {
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
    }
    require_columns(path, columns, required)
    reject_duplicate_rows(
        source_rows,
        dataset="HCP",
        key_fields=("date", "indicator", "category"),
    )
    validate_provenance_files(source_rows, "HCP")
    rows = []
    for source in source_rows:
        try:
            period = date.fromisoformat(source["date"])
        except ValueError as exc:
            raise WarehouseValidationError(
                [f"Date HCP invalide : {source['date']}"]
            ) from exc
        if period.day != 1:
            raise WarehouseValidationError([f"Date HCP non mensuelle : {period}"])
        if (
            source["indicator"] != "consumer_price_index"
            or source["unit"] != "index_points"
        ):
            raise WarehouseValidationError(
                ["L'IPC HCP doit rester un indice mensuel en index_points"]
            )
        rows.append(
            (
                period,
                source["indicator"],
                source["category"],
                parse_decimal(source["value"], "hcp.value", nullable=True),
                source["unit"],
                parse_int(source["base_year"], "base_year"),
                source["source"],
                source["source_file"],
                source["source_sha256"],
                source["source_sheet"],
                parse_int(source["source_row"], "source_row"),
            )
        )
    return rows, {
        "columns": columns,
        "row_count": len(rows),
        "missing_value_count": sum(row[3] is None for row in rows),
        "start_date": min(row[0] for row in rows).isoformat(),
        "end_date": max(row[0] for row in rows).isoformat(),
        "units": sorted({row[4] for row in rows}),
        "categories": sorted({row[2] for row in rows}),
        "quarantined_rows": 0,
    }


def load_worldbank(path: Path) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    columns, source_rows = read_csv(path)
    required = {
        "country",
        "year",
        "indicator",
        "value",
        "unit",
        "source",
        "source_file",
        "source_sha256",
    }
    require_columns(path, columns, required)
    reject_duplicate_rows(
        source_rows,
        dataset="World Bank",
        key_fields=("country", "year", "indicator"),
    )
    validate_provenance_files(source_rows, "World Bank")
    rows = []
    expected_indicators = {
        "gdp_growth_annual_pct",
        "inflation_consumer_prices_annual_pct",
    }
    for source in source_rows:
        if (
            source["country"] != "MAR"
            or source["unit"] != "percent"
            or source["indicator"] not in expected_indicators
        ):
            raise WarehouseValidationError(
                ["Sémantique World Bank incompatible (pays, indicateur ou unité)"]
            )
        rows.append(
            (
                source["country"],
                parse_int(source["year"], "worldbank.year"),
                source["indicator"],
                parse_decimal(source["value"], "worldbank.value", nullable=True),
                source["unit"],
                source["source"],
                source["source_file"],
                source["source_sha256"],
            )
        )
    return rows, {
        "columns": columns,
        "row_count": len(rows),
        "missing_value_count": sum(row[3] is None for row in rows),
        "start_year": min(row[1] for row in rows),
        "end_year": max(row[1] for row in rows),
        "units": sorted({row[4] for row in rows}),
        "indicators": sorted({row[2] for row in rows}),
        "quarantined_rows": 0,
    }


def source_hashes(inputs: WarehouseInputs) -> dict[str, str]:
    hashes = {}
    for name, path in inputs.__dict__.items():
        if not path.is_file():
            raise WarehouseValidationError([f"Source absente : {path}"])
        hashes[name] = sha256_file(path)
    hashes["schema"] = sha256_file(SCHEMA_FILE)
    hashes["example_queries"] = sha256_file(EXAMPLE_QUERIES_FILE)
    hashes["loader"] = sha256_file(Path(__file__))
    return hashes


def ingestion_fingerprint(hashes: dict[str, str]) -> str:
    payload = {
        "schema_version": WAREHOUSE_SCHEMA_VERSION,
        "duckdb_version": duckdb.__version__,
        "duckdb_memory_limit": DUCKDB_MEMORY_LIMIT,
        "duckdb_threads": DUCKDB_THREADS,
        "hashes": hashes,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def execute_examples(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    queries = [
        (
            "financial_coverage",
            (
                "SELECT company_id, verified_observation_count, "
                "verified_indicator_count FROM v_financial_coverage "
                "ORDER BY verified_observation_count DESC, company_id"
            ),
        ),
        (
            "financial_current_and_comparative",
            (
                "SELECT company_id, observation_year, indicator, "
                "accounting_perimeter, normalized_value, currency, "
                "is_comparative FROM v_financial_observations ORDER BY "
                "company_id, observation_year, indicator"
            ),
        ),
        (
            "hcp_latest_general_index",
            (
                "SELECT observation_date, value, unit, base_year "
                "FROM v_hcp_monthly_trend WHERE category = 'Indice Général' "
                "ORDER BY observation_date DESC LIMIT 12"
            ),
        ),
        (
            "worldbank_2023_2025",
            (
                "SELECT observation_year, indicator, value, unit "
                "FROM v_worldbank_annual_trend WHERE observation_year "
                "BETWEEN 2023 AND 2025 ORDER BY observation_year, indicator"
            ),
        ),
        (
            "source_granularity_counts",
            (
                "SELECT 'HCP_MONTHLY' AS series, count(*) AS observation_count "
                "FROM fact_hcp_monthly UNION ALL SELECT 'WORLDBANK_ANNUAL', "
                "count(*) FROM fact_worldbank_annual UNION ALL SELECT "
                "'AMMC_VERIFIED', count(*) FROM fact_financial_observations"
            ),
        ),
        (
            "s2m_2024_as_reported",
            (
                "SELECT company_id, observation_year, indicator, "
                "normalized_value, selection_status, "
                "selected_report_exercise_year FROM "
                "v_financial_as_reported WHERE company_id = 's2m' "
                "AND observation_year = 2024 ORDER BY indicator"
            ),
        ),
        (
            "s2m_2024_latest_available",
            (
                "SELECT company_id, observation_year, indicator, "
                "normalized_value, selection_status, "
                "selected_report_exercise_year FROM "
                "v_financial_latest_available WHERE company_id = 's2m' "
                "AND observation_year = 2024 ORDER BY indicator"
            ),
        ),
        (
            "financial_version_divergences",
            (
                "SELECT company_id, observation_year, indicator, "
                "first_value, latest_value, absolute_difference, "
                "relative_difference_pct, comparison_status FROM "
                "v_financial_version_comparison WHERE comparison_status = "
                "'DIVERGENT' ORDER BY company_id, observation_year, indicator"
            ),
        ),
    ]
    results = []
    for name, sql in queries:
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        results.append({"name": name, "sql": sql, "row_count": len(rows), "rows": rows})
    return results


def cached_report(
    database: Path, report_path: Path, fingerprint: str
) -> dict[str, Any] | None:
    if not database.is_file() or not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            report.get("ingestion_fingerprint") != fingerprint
            or report.get("database_sha256") != sha256_file(database)
        ):
            return None
        connection = duckdb.connect(str(database), read_only=True)
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "dim_company",
                "fact_financial_observations",
                "fact_hcp_monthly",
                "fact_worldbank_annual",
            )
        }
        connection.close()
        if counts != report.get("loaded_rows"):
            return None
    except (OSError, ValueError, duckdb.Error, json.JSONDecodeError):
        return None
    return {**report, "cache_used": True}


def build_warehouse(
    *,
    inputs: WarehouseInputs | None = None,
    database: Path = DEFAULT_DATABASE,
    report_path: Path = DEFAULT_REPORT,
    failed_report_path: Path = DEFAULT_FAILED_REPORT,
    query_results_path: Path = DEFAULT_QUERY_RESULTS,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    inputs = (inputs or WarehouseInputs()).resolved()
    database = Path(database).resolve()
    report_path = Path(report_path).resolve()
    failed_report_path = Path(failed_report_path).resolve()
    query_results_path = Path(query_results_path).resolve()
    temporary = database.with_suffix(database.suffix + ".building")
    try:
        hashes = source_hashes(inputs)
        fingerprint = ingestion_fingerprint(hashes)
        cached = cached_report(database, report_path, fingerprint)
        if cached is not None:
            return cached

        companies, company_audit = load_companies(inputs.companies)
        company_ids = {row[0] for row in companies}
        financial, financial_audit = load_financial(
            inputs.financial, inputs.references, inputs.manifest, company_ids
        )
        hcp, hcp_audit = load_hcp(inputs.hcp)
        worldbank, worldbank_audit = load_worldbank(inputs.worldbank)
        database.parent.mkdir(parents=True, exist_ok=True)
        temporary.unlink(missing_ok=True)
        connection = duckdb.connect(str(temporary))
        try:
            connection.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
            connection.execute(f"SET threads = {DUCKDB_THREADS}")
            connection.execute("BEGIN TRANSACTION")
            connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
            connection.executemany(
                "INSERT INTO dim_company VALUES (?, ?, ?, ?)", companies
            )
            connection.executemany(
                "INSERT INTO fact_financial_observations VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                financial,
            )
            connection.executemany(
                "INSERT INTO fact_hcp_monthly VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                hcp,
            )
            connection.executemany(
                "INSERT INTO fact_worldbank_annual VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                worldbank,
            )
            completed = datetime.now(UTC)
            connection.execute(
                "INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, 'COMMITTED')",
                [fingerprint, WAREHOUSE_SCHEMA_VERSION, started, completed],
            )
            source_rows = [
                (
                    fingerprint,
                    name,
                    str(getattr(inputs, name)),
                    hashes[name],
                    audit["row_count"],
                    audit["row_count"],
                    audit["quarantined_rows"],
                )
                for name, audit in (
                    ("financial", financial_audit),
                    ("hcp", hcp_audit),
                    ("worldbank", worldbank_audit),
                )
            ]
            connection.executemany(
                "INSERT INTO ingestion_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                source_rows,
            )
            expected_counts = {
                "dim_company": len(companies),
                "fact_financial_observations": len(financial),
                "fact_hcp_monthly": len(hcp),
                "fact_worldbank_annual": len(worldbank),
            }
            actual_counts = {
                table: connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in expected_counts
            }
            if actual_counts != expected_counts:
                raise WarehouseValidationError(
                    [f"Comptages avant commit invalides : {actual_counts}"]
                )
            non_verified = connection.execute(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE verification_status <> 'VERIFIED'"
            ).fetchone()[0]
            if non_verified:
                raise WarehouseValidationError(
                    ["Une observation financière non VERIFIED a atteint la table finale"]
                )
            example_results = execute_examples(connection)
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise
        finally:
            connection.close()
        os.replace(temporary, database)
        query_payload = {
            "schema_version": WAREHOUSE_SCHEMA_VERSION,
            "ingestion_fingerprint": fingerprint,
            "queries": example_results,
        }
        atomic_json(query_results_path, query_payload)
        report = {
            "schema_version": WAREHOUSE_SCHEMA_VERSION,
            "status": "COMMITTED",
            "cache_used": False,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "ingestion_fingerprint": fingerprint,
            "database": str(database),
            "database_sha256": sha256_file(database),
            "runtime": {
                "duckdb_version": duckdb.__version__,
                "memory_limit": DUCKDB_MEMORY_LIMIT,
                "threads": DUCKDB_THREADS,
            },
            "source_sha256": hashes,
            "loaded_rows": actual_counts,
            "audit": {
                "dim_company": company_audit,
                "financial": financial_audit,
                "hcp": hcp_audit,
                "worldbank": worldbank_audit,
            },
            "quality": {
                "critical_error_count": 0,
                "total_quarantined_rows": 0,
                "non_verified_financial_rows_loaded": 0,
                "financial_combination_explanation": (
                    f"Les {len(financial)} observations conservent à la fois "
                    "l'exercice du rapport, la période observée et le marqueur "
                    "comparatif. Une valeur comparative publiée dans un rapport "
                    "ultérieur ne remplace donc pas silencieusement la valeur du "
                    "rapport de l'exercice d'origine."
                ),
                "macro_semantic_separation": (
                    "L'IPC HCP mensuel en points d'indice et l'inflation World "
                    "Bank annuelle en pourcentage restent dans deux faits et deux vues."
                ),
            },
            "views": [
                "v_financial_observations",
                "v_financial_coverage",
                "v_hcp_monthly_trend",
                "v_worldbank_annual_trend",
            ],
            "query_results": str(query_results_path),
            "query_results_sha256": sha256_file(query_results_path),
        }
        atomic_json(report_path, report)
        failed_report_path.unlink(missing_ok=True)
        return report
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        issues = exc.issues if isinstance(exc, WarehouseValidationError) else [str(exc)]
        atomic_json(
            failed_report_path,
            {
                "schema_version": WAREHOUSE_SCHEMA_VERSION,
                "status": "FAILED",
                "started_at": started.isoformat(),
                "database_preserved": database.is_file(),
                "issues": issues,
            },
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--failed-report", type=Path, default=DEFAULT_FAILED_REPORT)
    parser.add_argument("--query-results", type=Path, default=DEFAULT_QUERY_RESULTS)
    args = parser.parse_args()
    report = build_warehouse(
        database=args.database,
        report_path=args.report,
        failed_report_path=args.failed_report,
        query_results_path=args.query_results,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
