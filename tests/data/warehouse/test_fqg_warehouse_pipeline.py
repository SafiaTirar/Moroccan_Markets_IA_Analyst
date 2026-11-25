from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from moroccan_markets_ai.data.warehouse import fqg_warehouse_pipeline as pipeline
from moroccan_markets_ai.data.warehouse.fqg_warehouse_pipeline import (
    DEFAULT_FQG_HANDOFF,
    PipelinePaths,
    build_parser,
    run_pipeline,
    validate_database,
)
from moroccan_markets_ai.data.warehouse.loader import (
    SCHEMA_FILE,
    WarehouseValidationError,
    execute_examples,
    sha256_file,
)


def build_validation_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    database = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO dim_company VALUES (?, ?, ?, ?)",
        ["company_0", "Company 0", "Test", "[]"],
    )
    connection.execute(
        "INSERT INTO fact_financial_observations VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "reference-1",
            "company_0",
            2025,
            "2025",
            2025,
            False,
            "NET_INCOME",
            "123450",
            "MAD",
            1000,
            "CONSOLIDATED",
            "a" * 64,
            7,
            "DOCLING_001",
            "VERIFIED",
        ],
    )
    connection.execute(
        "INSERT INTO fact_hcp_monthly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "2025-01-01",
            "consumer_price_index",
            "Indice Général",
            "118.5",
            "index_points",
            2017,
            "https://hcp.example",
            "hcp.csv",
            "b" * 64,
            "monthly",
            1,
        ],
    )
    connection.execute(
        "INSERT INTO fact_worldbank_annual VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "MAR",
            2025,
            "gdp_growth_annual_pct",
            "4.5",
            "percent",
            "https://worldbank.example",
            "worldbank.json",
            "c" * 64,
        ],
    )
    queries = execute_examples(connection)
    connection.close()

    ingestion_report = tmp_path / "ingestion.json"
    ingestion_report.write_text(
        json.dumps(
            {
                "status": "COMMITTED",
                "database_sha256": sha256_file(database),
                "loaded_rows": {
                    "fact_financial_observations": 1,
                    "fact_hcp_monthly": 1,
                    "fact_worldbank_annual": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    query_results = tmp_path / "queries.json"
    query_results.write_text(
        json.dumps({"queries": queries}, default=str), encoding="utf-8"
    )
    validation_report = tmp_path / "validation.json"
    return database, ingestion_report, query_results, validation_report


def test_pipeline_defaults_to_final_fqg_handoff() -> None:
    paths = PipelinePaths()

    assert paths.candidates == DEFAULT_FQG_HANDOFF / "candidats_fqg.json"
    assert paths.anomalies == DEFAULT_FQG_HANDOFF / "anomalies.json"
    assert paths.bilan == DEFAULT_FQG_HANDOFF / "bilan.json"
    assert DEFAULT_FQG_HANDOFF.name == "handoff_fqg_v2_final"


def test_display_path_keeps_repository_reports_portable() -> None:
    displayed = pipeline._display_path(
        DEFAULT_FQG_HANDOFF / "candidats_fqg.json"
    )

    assert displayed == (
        "data/quality/ammc/handoff_fqg_v2_final/candidats_fqg.json"
    )
    assert not displayed.startswith("/")


def test_cli_accepts_explicit_fqg_inputs(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.json"
    anomalies = tmp_path / "anomalies.json"
    bilan = tmp_path / "bilan.json"
    database = tmp_path / "warehouse.duckdb"
    ingestion = tmp_path / "ingestion.json"
    failed = tmp_path / "failed.json"
    queries = tmp_path / "queries.json"
    validation = tmp_path / "validation.json"
    pipeline_report = tmp_path / "pipeline.json"

    args = build_parser().parse_args(
        [
            "--candidates",
            str(candidates),
            "--anomalies",
            str(anomalies),
            "--bilan",
            str(bilan),
            "--database",
            str(database),
            "--ingestion-report",
            str(ingestion),
            "--failed-report",
            str(failed),
            "--query-results",
            str(queries),
            "--validation-report",
            str(validation),
            "--pipeline-report",
            str(pipeline_report),
        ]
    )

    assert args.candidates == candidates
    assert args.anomalies == anomalies
    assert args.bilan == bilan
    assert args.database == database
    assert args.ingestion_report == ingestion
    assert args.failed_report == failed
    assert args.query_results == queries
    assert args.validation_report == validation
    assert args.pipeline_report == pipeline_report


def test_final_handoff_contains_expected_fqg_volume() -> None:
    candidates = json.loads(
        (DEFAULT_FQG_HANDOFF / "candidats_fqg.json").read_text(encoding="utf-8")
    )

    assert len(candidates) == 327


def test_final_handoff_produces_expected_fqg_contract(tmp_path: Path) -> None:
    paths = PipelinePaths()

    report = pipeline.run_fqg(
        candidates=paths.candidates,
        anomalies_path=paths.anomalies,
        output=tmp_path / "fqg",
        bilan_path=paths.bilan,
        human_decisions=paths.human_decisions,
        pdf_sources=paths.pdf_sources,
    )

    assert report["candidates_received"] == 327
    assert report["structurally_valid"] == 327
    assert report["verified"] == 47
    assert report["pdf_confirmed"] == 37
    assert report["rejected"] == 0
    assert report["obsolete_human_decisions"] == 8


def test_validate_database_accepts_consistent_transaction(tmp_path: Path) -> None:
    database, ingestion, queries, output = build_validation_fixture(tmp_path)

    report = validate_database(
        database=database,
        ingestion_report=ingestion,
        query_results=queries,
        output=output,
    )

    assert report["status"] == "PASSED"
    assert report["checks"]["financial_rows"] == 1
    assert report["checks"]["analytic_queries"] == 8
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "PASSED"


def test_validate_database_rejects_report_for_another_database(
    tmp_path: Path,
) -> None:
    database, ingestion, queries, output = build_validation_fixture(tmp_path)
    payload = json.loads(ingestion.read_text(encoding="utf-8"))
    payload["database_sha256"] = "0" * 64
    ingestion.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WarehouseValidationError, match="Validation finale"):
        validate_database(
            database=database,
            ingestion_report=ingestion,
            query_results=queries,
            output=output,
        )

    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FAILED"


def test_pipeline_preserves_published_database_when_final_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = {}
    for name in (
        "candidates",
        "anomalies",
        "bilan",
        "human_decisions",
        "pdf_sources",
        "base_financial",
        "base_references",
        "companies",
        "manifest",
        "hcp",
        "worldbank",
    ):
        path = tmp_path / f"{name}.json"
        path.write_text("{}", encoding="utf-8")
        inputs[name] = path
    database = tmp_path / "published.duckdb"
    database.write_bytes(b"published-database")
    paths = PipelinePaths(
        **inputs,
        run_root=tmp_path / "run",
        database=database,
        ingestion_report=tmp_path / "ingestion.json",
        failed_report=tmp_path / "failed.json",
        query_results=tmp_path / "queries.json",
        validation_report=tmp_path / "validation.json",
        pipeline_report=tmp_path / "pipeline.json",
    )

    monkeypatch.setattr(
        pipeline,
        "run_fqg",
        lambda **kwargs: {"candidates_received": 1, "verified": 1},
    )
    monkeypatch.setattr(
        pipeline,
        "adapt_fqg_export",
        lambda **kwargs: {
            "fqg_input": str(tmp_path / "fqg.jsonl"),
            "financial_output": str(tmp_path / "financial.csv"),
            "references_output": str(tmp_path / "references.json"),
        },
    )
    monkeypatch.setattr(
        pipeline,
        "combine_financial_exports",
        lambda *args: ([{}], {"base_rows": 0, "increment_rows": 1}),
    )
    monkeypatch.setattr(
        pipeline,
        "combine_reference_files",
        lambda *args: {"combined_references": 1},
    )

    def fake_build(**kwargs):
        Path(kwargs["database"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["database"]).write_bytes(b"candidate-database")
        return {"status": "COMMITTED", "loaded_rows": {}}

    monkeypatch.setattr(pipeline, "build_warehouse", fake_build)
    monkeypatch.setattr(
        pipeline,
        "validate_database",
        lambda **kwargs: (_ for _ in ()).throw(
            WarehouseValidationError(["final validation failed"])
        ),
    )

    with pytest.raises(WarehouseValidationError, match="final validation failed"):
        run_pipeline(paths)

    assert database.read_bytes() == b"published-database"
