from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.processing import corpus_extension_runner as runner


def audit_row(
    company_id: str,
    year: int,
    classification: str = "TEXT",
    workload: int = 10,
) -> dict[str, str]:
    return {
        "company_id": company_id,
        "issuer": company_id,
        "exercise_year": str(year),
        "audit_status": "SUCCESS",
        "manifest_valid": "true",
        "document_classification": classification,
        "page_count": "10",
        "actual_size_bytes": "1000",
        "sha256": "a" * 64,
        "local_path": f"data/{company_id}_{year}.pdf",
        "ocr_candidate_pages": "1",
        "financial_candidate_pages": "5",
        "estimated_workload_units": str(workload),
        "recommended_strategy": "DOCLING",
    }


def test_extension_selects_only_the_five_authorized_unprocessed_reports() -> None:
    keys = [
        ("auto_hall", 2024, "TEXT"),
        ("s2m", 2023, "MIXED"),
        ("sothema", 2025, "TEXT"),
        ("wafa_assurance", 2024, "TEXT"),
        ("attijariwafa_bank", 2024, "TEXT"),
    ]
    rows = [audit_row(company, year, kind) for company, year, kind in keys]
    processed = [
        {"company_id": "s2m", "exercise_year": 2025},
        {"company_id": "auto_hall", "exercise_year": 2025},
    ]

    selected = runner.select_extension(rows, rows, processed)

    assert [
        (item["company_id"], int(item["exercise_year"])) for item in selected
    ] == [(company, year) for company, year, _ in keys]
    assert len(selected) == runner.MAX_DOCUMENTS


def test_extension_rejects_a_report_already_processed() -> None:
    keys = [
        ("auto_hall", 2024, "TEXT"),
        ("s2m", 2023, "MIXED"),
        ("sothema", 2025, "TEXT"),
        ("wafa_assurance", 2024, "TEXT"),
        ("attijariwafa_bank", 2024, "TEXT"),
    ]
    rows = [audit_row(company, year, kind) for company, year, kind in keys]

    with pytest.raises(ValueError, match="déjà traité"):
        runner.select_extension(
            rows,
            rows,
            [{"company_id": "s2m", "exercise_year": 2023}],
        )


def test_processed_documents_are_identified_by_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = []
    for index in range(8):
        source = tmp_path / f"report_{index}.pdf"
        source.write_bytes(f"report-{index}".encode())
        documents.append(
            {
                "company_id": f"company_{index}",
                "exercise_year": 2025,
                "source_pdf": source.name,
                "source_pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    references = tmp_path / "references.json"
    references.write_text(json.dumps({"documents": documents}), encoding="utf-8")
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)

    result = runner.processed_documents(references)

    assert len(result) == 8
    documents[0]["source_pdf_sha256"] = "0" * 64
    references.write_text(json.dumps({"documents": documents}), encoding="utf-8")
    with pytest.raises(ValueError, match="absent ou modifié"):
        runner.processed_documents(references)
