from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.warehouse.fqg_adapter import (
    FINANCIAL_FIELDS,
    adapt_fqg_export,
)
from moroccan_markets_ai.data.warehouse.loader import (
    WarehouseValidationError,
    load_financial,
)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.fixture
def adapter_fixture(tmp_path: Path) -> dict[str, Path | dict]:
    sha256 = "a" * 64
    companies = tmp_path / "companies.json"
    companies.write_text(
        json.dumps(
            {
                "companies": [
                    {
                        "company_id": f"company_{index}",
                        "ammc_issuer": f"Company {index}",
                        "sector": "Test",
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
        [{"company_id": "company_0", "exercise_year": "2025", "sha256": sha256}],
    )
    record = {
        "candidate_id": "1234567890abcdef",
        "status": "VERIFIED",
        "normalized": {
            "company": "company_0",
            "indicator": "NET_INCOME",
            "label": "Résultat net",
            "observation_year": 2024,
            "report_year": 2025,
            "value": "123.45",
            "source_table": "DOCLING_001",
            "source_page": 7,
        },
        "evidence": {
            "pdf_file": "company_0_2025.pdf",
            "pdf_sha256": sha256,
            "pdf_page": 7,
            "value_read": "123,45",
            "sign": "+",
            "unit": "KMAD",
            "scope": "CONSOLIDE",
            "column_year": 2024,
            "currency": "MAD",
            "checked_by": "Reviewer",
            "checked_at": "2026-11-10",
        },
    }
    return {
        "companies": companies,
        "manifest": manifest,
        "record": record,
        "fqg": tmp_path / "admissible.jsonl",
        "financial": tmp_path / "financial.csv",
        "references": tmp_path / "references.json",
        "audit": tmp_path / "audit.json",
    }


def run_adapter(paths: dict[str, Path | dict], **overrides) -> dict:
    arguments = {
        "fqg_path": paths["fqg"],
        "financial_output": paths["financial"],
        "references_output": paths["references"],
        "audit_output": paths["audit"],
        "companies_path": paths["companies"],
        "manifest_path": paths["manifest"],
        **overrides,
    }
    return adapt_fqg_export(**arguments)


def test_adapter_normalizes_verified_value_and_builds_reference(
    adapter_fixture: dict[str, Path | dict],
) -> None:
    write_jsonl(adapter_fixture["fqg"], [adapter_fixture["record"]])

    audit = run_adapter(adapter_fixture)

    with adapter_fixture["financial"].open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    references = json.loads(adapter_fixture["references"].read_text(encoding="utf-8"))
    assert audit["emitted_records"] == 1
    assert list(rows[0]) == FINANCIAL_FIELDS
    assert rows[0]["normalized_value"] == "123450"
    assert rows[0]["scale"] == "1000"
    assert rows[0]["accounting_perimeter"] == "CONSOLIDATED"
    observation = references["documents"][0]["observations"][0]
    assert observation["period"] == "2024"
    assert observation["comparative_period"] is True
    assert observation["reliable"] is True
    assert observation["expected_status"] == "VERIFIED"
    warehouse_rows, warehouse_audit = load_financial(
        adapter_fixture["financial"],
        adapter_fixture["references"],
        adapter_fixture["manifest"],
        {f"company_{index}" for index in range(15)},
    )
    assert len(warehouse_rows) == 1
    assert warehouse_audit["verification_statuses"] == {"VERIFIED": 1}


def test_adapter_skips_identical_business_value_from_existing_warehouse(
    adapter_fixture: dict[str, Path | dict], tmp_path: Path
) -> None:
    record = adapter_fixture["record"]
    write_jsonl(adapter_fixture["fqg"], [record])
    existing_financial = tmp_path / "existing.csv"
    write_csv(
        existing_financial,
        FINANCIAL_FIELDS,
        [
            {
                "company_id": "company_0",
                "exercise_year": "2025",
                "indicator": "NET_INCOME",
                "normalized_value": "123450",
                "currency": "MAD",
                "scale": "1000",
                "accounting_perimeter": "CONSOLIDATED",
                "source_pdf_sha256": "a" * 64,
                "page_number": "7",
                "table_id": "OLD",
                "verification_reference_id": "existing-reference",
                "verification_status": "VERIFIED",
            }
        ],
    )
    existing_references = tmp_path / "existing.json"
    existing_references.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "company_id": "company_0",
                        "exercise_year": 2025,
                        "observations": [
                            {
                                "reference_id": "existing-reference",
                                "period": "2024",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    audit = run_adapter(
        adapter_fixture,
        existing_financial=existing_financial,
        existing_references=existing_references,
    )

    assert audit["emitted_records"] == 0
    assert audit["skipped_existing_duplicates"] == 1
    with adapter_fixture["financial"].open(encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == []


def test_adapter_is_idempotent_when_reference_already_exists(
    adapter_fixture: dict[str, Path | dict], tmp_path: Path
) -> None:
    record = adapter_fixture["record"]
    write_jsonl(adapter_fixture["fqg"], [record])
    existing_financial = tmp_path / "existing.csv"
    existing_reference_id = record["candidate_id"]
    write_csv(
        existing_financial,
        FINANCIAL_FIELDS,
        [
            {
                "company_id": "company_0",
                "exercise_year": "2025",
                "indicator": "NET_INCOME",
                "normalized_value": "123450",
                "currency": "MAD",
                "scale": "1000",
                "accounting_perimeter": "CONSOLIDATED",
                "source_pdf_sha256": "a" * 64,
                "page_number": "7",
                "table_id": "DOCLING_001",
                "verification_reference_id": existing_reference_id,
                "verification_status": "VERIFIED",
            }
        ],
    )
    existing_references = tmp_path / "existing.json"
    existing_references.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "company_id": "company_0",
                        "exercise_year": 2025,
                        "observations": [
                            {"reference_id": existing_reference_id, "period": "2024"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    audit = run_adapter(
        adapter_fixture,
        existing_financial=existing_financial,
        existing_references=existing_references,
    )

    assert audit["emitted_records"] == 0
    assert audit["skipped_reference_ids"] == [existing_reference_id]


def test_adapter_rejects_value_conflicting_with_existing_warehouse(
    adapter_fixture: dict[str, Path | dict], tmp_path: Path
) -> None:
    write_jsonl(adapter_fixture["fqg"], [adapter_fixture["record"]])
    existing_financial = tmp_path / "existing.csv"
    existing_row = {
        field: "" for field in FINANCIAL_FIELDS
    }
    existing_row.update(
        {
            "company_id": "company_0",
            "exercise_year": "2025",
            "indicator": "NET_INCOME",
            "normalized_value": "999",
            "currency": "MAD",
            "accounting_perimeter": "CONSOLIDATED",
            "verification_reference_id": "existing-reference",
        }
    )
    write_csv(existing_financial, FINANCIAL_FIELDS, [existing_row])
    existing_references = tmp_path / "existing.json"
    existing_references.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "company_id": "company_0",
                        "exercise_year": 2025,
                        "observations": [
                            {"reference_id": "existing-reference", "period": "2024"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WarehouseValidationError, match="contradictoire"):
        run_adapter(
            adapter_fixture,
            existing_financial=existing_financial,
            existing_references=existing_references,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row.update(status="PDF_CONFIRMED"), "Statut FQG non admissible"),
        (lambda row: row["evidence"].update(unit=None), "evidence.unit"),
        (lambda row: row["evidence"].update(scope=None), "evidence.scope"),
        (lambda row: row["evidence"].update(pdf_sha256="b" * 64), "absent du manifeste"),
        (lambda row: row["evidence"].update(value_read="999"), "divergentes"),
        (lambda row: row["evidence"].update(column_year=2023), "Année de colonne"),
    ],
)
def test_adapter_rejects_unusable_fqg_proof(
    adapter_fixture: dict[str, Path | dict], mutation, message: str
) -> None:
    record = json.loads(json.dumps(adapter_fixture["record"]))
    mutation(record)
    write_jsonl(adapter_fixture["fqg"], [record])

    with pytest.raises(WarehouseValidationError, match=message):
        run_adapter(adapter_fixture)
