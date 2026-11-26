"""Pipeline reproductible FQG vers le Data Warehouse DuckDB.

Commande principale : ``uv run fqg-warehouse``.
La base publiée n'est remplacée qu'après une construction transactionnelle
réussie et un ensemble strict de contrôles financiers.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from moroccan_markets_ai.data.parsers.fqg_pipeline import run as run_fqg
from moroccan_markets_ai.data.parsers.fqg_source_verification import (
    DEFAULT_PDF_SOURCES,
)
from moroccan_markets_ai.data.warehouse.fqg_adapter import adapt_fqg_export
from moroccan_markets_ai.data.warehouse.incremental_loader import (
    combine_financial_exports,
    combine_reference_files,
)
from moroccan_markets_ai.data.warehouse.loader import (
    DEFAULT_COMPANIES,
    DEFAULT_HCP,
    DEFAULT_MANIFEST,
    DEFAULT_WORLDBANK,
    PROJECT_ROOT,
    WarehouseInputs,
    WarehouseValidationError,
    atomic_json,
    build_warehouse,
    execute_examples,
    sha256_file,
)

DEFAULT_HUMAN_DECISIONS = (
    PROJECT_ROOT / "data/quality/ammc/fqg_validation/human_decisions.json"
)
DEFAULT_FQG_HANDOFF = (
    PROJECT_ROOT / "data/quality/ammc/handoff_fqg_v2_final"
)
DEFAULT_BASE_FINANCIAL = (
    PROJECT_ROOT
    / "data/quality/ammc/data_06_2/verified_financial_observations.csv"
)
DEFAULT_BASE_REFERENCES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json"
)
DEFAULT_RUN_ROOT = PROJECT_ROOT / "data/quality/ammc/fqg_pipeline_run"
DEFAULT_DATABASE = PROJECT_ROOT / "data/warehouse/moroccan_markets_fqg.duckdb"
DEFAULT_INGESTION_REPORT = PROJECT_ROOT / "data/warehouse/fqg_ingestion_report.json"
DEFAULT_FAILED_REPORT = (
    PROJECT_ROOT / "data/warehouse/fqg_last_failed_ingestion.json"
)
DEFAULT_QUERY_RESULTS = (
    PROJECT_ROOT / "data/warehouse/fqg_example_query_results.json"
)
DEFAULT_VALIDATION_REPORT = (
    PROJECT_ROOT / "data/warehouse/fqg_validation_report.json"
)
DEFAULT_PIPELINE_REPORT = PROJECT_ROOT / "data/warehouse/fqg_pipeline_report.json"


@dataclass(frozen=True)
class PipelinePaths:
    """Entrées et sorties explicites du pipeline reproductible."""

    candidates: Path = DEFAULT_FQG_HANDOFF / "candidats_fqg.json"
    anomalies: Path = DEFAULT_FQG_HANDOFF / "anomalies.json"
    bilan: Path = DEFAULT_FQG_HANDOFF / "bilan.json"
    human_decisions: Path = DEFAULT_HUMAN_DECISIONS
    pdf_sources: Path = DEFAULT_PDF_SOURCES
    base_financial: Path = DEFAULT_BASE_FINANCIAL
    base_references: Path = DEFAULT_BASE_REFERENCES
    companies: Path = DEFAULT_COMPANIES
    manifest: Path = DEFAULT_MANIFEST
    hcp: Path = DEFAULT_HCP
    worldbank: Path = DEFAULT_WORLDBANK
    run_root: Path = DEFAULT_RUN_ROOT
    database: Path = DEFAULT_DATABASE
    ingestion_report: Path = DEFAULT_INGESTION_REPORT
    failed_report: Path = DEFAULT_FAILED_REPORT
    query_results: Path = DEFAULT_QUERY_RESULTS
    validation_report: Path = DEFAULT_VALIDATION_REPORT
    pipeline_report: Path = DEFAULT_PIPELINE_REPORT


def _display_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _validate_inputs(paths: PipelinePaths) -> None:
    required = {
        "candidates": paths.candidates,
        "anomalies": paths.anomalies,
        "bilan": paths.bilan,
        "human_decisions": paths.human_decisions,
        "pdf_sources": paths.pdf_sources,
        "base_financial": paths.base_financial,
        "base_references": paths.base_references,
        "companies": paths.companies,
        "manifest": paths.manifest,
        "hcp": paths.hcp,
        "worldbank": paths.worldbank,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise WarehouseValidationError(
            ["Entrées du pipeline absentes", *missing]
        )


def validate_database(
    *,
    database: Path,
    ingestion_report: Path,
    query_results: Path,
    output: Path,
) -> dict[str, Any]:
    """Contrôle la base finale et écrit un rapport ``PASSED`` ou ``FAILED``."""

    ingestion = json.loads(Path(ingestion_report).read_text(encoding="utf-8"))
    stored_queries = json.loads(Path(query_results).read_text(encoding="utf-8"))
    connection = duckdb.connect(str(database), read_only=True)
    try:
        scalar = lambda sql: connection.execute(sql).fetchone()[0]
        executed_queries = execute_examples(connection)
        checks = {
            "database_sha_matches_report": (
                sha256_file(database) == ingestion.get("database_sha256")
            ),
            "ingestion_status": ingestion.get("status"),
            "financial_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations"
            ),
            "non_verified_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE verification_status <> 'VERIFIED'"
            ),
            "duplicate_business_keys": scalar(
                "SELECT count(*) FROM ("
                "SELECT 1 FROM fact_financial_observations GROUP BY "
                "company_id, report_exercise_year, observation_year, indicator, "
                "accounting_perimeter, currency HAVING count(*) > 1)"
            ),
            "invalid_currency_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE currency <> 'MAD'"
            ),
            "invalid_scale_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE source_scale NOT IN (1, 1000, 1000000)"
            ),
            "invalid_scope_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE accounting_perimeter NOT IN ('SOCIAL', 'CONSOLIDATED')"
            ),
            "missing_provenance_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations WHERE "
                "source_pdf_sha256 IS NULL OR length(source_pdf_sha256) <> 64 "
                "OR page_number <= 0 OR table_id IS NULL"
            ),
            "current_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE NOT is_comparative"
            ),
            "comparative_rows": scalar(
                "SELECT count(*) FROM fact_financial_observations "
                "WHERE is_comparative"
            ),
            "companies_with_financial_data": scalar(
                "SELECT count(DISTINCT company_id) "
                "FROM fact_financial_observations"
            ),
            "hcp_rows": scalar("SELECT count(*) FROM fact_hcp_monthly"),
            "worldbank_rows": scalar("SELECT count(*) FROM fact_worldbank_annual"),
            "analytic_queries": len(executed_queries),
            "analytic_query_failures": [
                result["name"]
                for result in executed_queries
                if result["row_count"] != len(result["rows"])
            ],
            "query_names_match_report": (
                [item["name"] for item in executed_queries]
                == [item["name"] for item in stored_queries.get("queries", [])]
            ),
        }
    finally:
        connection.close()

    expected = ingestion.get("loaded_rows", {})
    checks["counts_match_ingestion_report"] = (
        checks["financial_rows"] == expected.get("fact_financial_observations")
        and checks["hcp_rows"] == expected.get("fact_hcp_monthly")
        and checks["worldbank_rows"] == expected.get("fact_worldbank_annual")
    )
    passed = (
        checks["database_sha_matches_report"]
        and checks["ingestion_status"] == "COMMITTED"
        and checks["counts_match_ingestion_report"]
        and checks["non_verified_rows"] == 0
        and checks["duplicate_business_keys"] == 0
        and checks["invalid_currency_rows"] == 0
        and checks["invalid_scale_rows"] == 0
        and checks["invalid_scope_rows"] == 0
        and checks["missing_provenance_rows"] == 0
        and not checks["analytic_query_failures"]
        and checks["query_names_match_report"]
    )
    report = {
        "schema_version": "1.0",
        "status": "PASSED" if passed else "FAILED",
        "database": _display_path(database),
        "database_sha256": sha256_file(database),
        "checks": {**checks, "passed": passed},
    }
    atomic_json(output, report)
    if not passed:
        raise WarehouseValidationError(["Validation finale DuckDB échouée"])
    return report


def run_pipeline(paths: PipelinePaths | None = None) -> dict[str, Any]:
    """Exécute FQG, adaptation, fusion, construction et validation."""

    paths = paths or PipelinePaths()
    _validate_inputs(paths)
    fqg_output = paths.run_root / "fqg_validation"
    adapter_output = paths.run_root / "fqg_warehouse"
    combined_output = paths.run_root / "combined"
    candidate_database = paths.run_root / "candidate.duckdb"
    candidate_ingestion_report = paths.run_root / "candidate_ingestion_report.json"
    candidate_failed_report = paths.run_root / "candidate_failed_ingestion.json"
    candidate_query_results = paths.run_root / "candidate_query_results.json"
    candidate_validation_report = paths.run_root / "candidate_validation_report.json"

    fqg_report = run_fqg(
        candidates=paths.candidates,
        anomalies_path=paths.anomalies,
        output=fqg_output,
        bilan_path=paths.bilan,
        human_decisions=paths.human_decisions,
        pdf_sources=paths.pdf_sources,
    )
    adapter_report = adapt_fqg_export(
        fqg_path=fqg_output / "admissible_values.jsonl",
        financial_output=adapter_output / "verified_financial_observations.csv",
        references_output=adapter_output / "financial_references.json",
        audit_output=adapter_output / "fqg_adapter_report.json",
        companies_path=paths.companies,
        manifest_path=paths.manifest,
        existing_financial=paths.base_financial,
        existing_references=paths.base_references,
    )
    financial_rows, financial_merge = combine_financial_exports(
        paths.base_financial,
        adapter_output / "verified_financial_observations.csv",
        combined_output / "verified_financial_observations.csv",
    )
    reference_merge = combine_reference_files(
        paths.base_references,
        adapter_output / "financial_references.json",
        combined_output / "financial_references.json",
    )
    warehouse_report = build_warehouse(
        inputs=WarehouseInputs(
            hcp=paths.hcp,
            worldbank=paths.worldbank,
            financial=combined_output / "verified_financial_observations.csv",
            companies=paths.companies,
            references=combined_output / "financial_references.json",
            manifest=paths.manifest,
        ),
        database=candidate_database,
        report_path=candidate_ingestion_report,
        failed_report_path=candidate_failed_report,
        query_results_path=candidate_query_results,
    )
    validation_report = validate_database(
        database=candidate_database,
        ingestion_report=candidate_ingestion_report,
        query_results=candidate_query_results,
        output=candidate_validation_report,
    )
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    paths.query_results.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate_database, paths.database)
    os.replace(candidate_query_results, paths.query_results)
    warehouse_report = {
        **warehouse_report,
        "database": _display_path(paths.database),
        "query_results": _display_path(paths.query_results),
    }
    validation_report = {
        **validation_report,
        "database": _display_path(paths.database),
    }
    atomic_json(paths.ingestion_report, warehouse_report)
    atomic_json(paths.validation_report, validation_report)
    paths.failed_report.unlink(missing_ok=True)
    adapter_stage = {**adapter_report}
    for key in ("fqg_input", "financial_output", "references_output"):
        adapter_stage[key] = _display_path(Path(adapter_stage[key]))
    report = {
        "schema_version": "1.0",
        "status": "PASSED",
        "command": "uv run fqg-warehouse",
        "stages": {
            "fqg": {
                "candidates_received": fqg_report["candidates_received"],
                "verified": fqg_report["verified"],
            },
            "adapter": adapter_stage,
            "merge": {
                **financial_merge,
                **reference_merge,
                "combined_financial_rows": len(financial_rows),
            },
            "warehouse": {
                "status": warehouse_report["status"],
                "cache_used": warehouse_report.get("cache_used", False),
                "loaded_rows": warehouse_report["loaded_rows"],
            },
            "validation": validation_report,
        },
    }
    atomic_json(paths.pipeline_report, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_FQG_HANDOFF / "candidats_fqg.json",
        help="Candidats du handoff FQG à intégrer.",
    )
    parser.add_argument(
        "--anomalies",
        type=Path,
        default=DEFAULT_FQG_HANDOFF / "anomalies.json",
        help="Anomalies associées aux candidats FQG.",
    )
    parser.add_argument(
        "--bilan",
        type=Path,
        default=DEFAULT_FQG_HANDOFF / "bilan.json",
        help="Bilan d'extraction associé au handoff FQG.",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument(
        "--human-decisions", type=Path, default=DEFAULT_HUMAN_DECISIONS
    )
    parser.add_argument("--pdf-sources", type=Path, default=DEFAULT_PDF_SOURCES)
    parser.add_argument(
        "--base-financial", type=Path, default=DEFAULT_BASE_FINANCIAL
    )
    parser.add_argument(
        "--base-references", type=Path, default=DEFAULT_BASE_REFERENCES
    )
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--hcp", type=Path, default=DEFAULT_HCP)
    parser.add_argument("--worldbank", type=Path, default=DEFAULT_WORLDBANK)
    parser.add_argument(
        "--ingestion-report", type=Path, default=DEFAULT_INGESTION_REPORT
    )
    parser.add_argument("--failed-report", type=Path, default=DEFAULT_FAILED_REPORT)
    parser.add_argument("--query-results", type=Path, default=DEFAULT_QUERY_RESULTS)
    parser.add_argument(
        "--validation-report", type=Path, default=DEFAULT_VALIDATION_REPORT
    )
    parser.add_argument(
        "--pipeline-report", type=Path, default=DEFAULT_PIPELINE_REPORT
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_pipeline(
        PipelinePaths(
            candidates=args.candidates,
            anomalies=args.anomalies,
            bilan=args.bilan,
            pdf_sources=args.pdf_sources,
            base_financial=args.base_financial,
            base_references=args.base_references,
            companies=args.companies,
            manifest=args.manifest,
            hcp=args.hcp,
            worldbank=args.worldbank,
            database=args.database,
            run_root=args.run_root,
            human_decisions=args.human_decisions,
            ingestion_report=args.ingestion_report,
            failed_report=args.failed_report,
            query_results=args.query_results,
            validation_report=args.validation_report,
            pipeline_report=args.pipeline_report,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
