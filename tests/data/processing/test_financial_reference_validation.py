import copy
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from moroccan_markets_ai.data.processing.financial_reference_validation import (
    DEFAULT_REFERENCE_FILE,
    _validate_context,
    run_reference_validation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def make_pdf(path: Path) -> str:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "ACME 2025 Comptes sociaux En MAD Total actif 100",
    )
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observation() -> dict:
    return {
        "reference_id": "acme-total-assets",
        "indicator": "TOTAL_ASSETS",
        "table_id": "DOCLING_001",
        "selector": {
            "table_id": "DOCLING_001",
            "label": "Total actif",
            "column": "2025",
            "page": [1],
        },
        "source_label": "Total actif",
        "reference_value": "100",
        "normalized_value": "100",
        "currency": "MAD",
        "unit": "MAD",
        "scale": 1,
        "accounting_perimeter": "SOCIAL",
        "period": "2025",
        "page_number": 1,
        "reliable": True,
        "expected_status": "VERIFIED",
        "verification_method": "DIRECT_PDF_TEXT_AND_VISUAL_COMPARISON",
        "verification_evidence": [
            {"mode": "TEXT_SEARCH", "page_number": 1, "quote": "Total actif"}
        ],
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scale", 1000, "unité et facteur"),
        ("period", "2024", "exercice et période"),
        ("accounting_perimeter", "UNKNOWN", "périmètre comptable"),
    ],
)
def test_reference_context_rejects_unit_period_and_scope_errors(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    pdf_path = tmp_path / "source.pdf"
    make_pdf(pdf_path)
    candidate = observation()
    candidate[field] = value
    document_spec = {"exercise_year": 2025}

    with (
        pymupdf.open(pdf_path) as document,
        pytest.raises(ValueError, match=message),
    ):
        _validate_context(document, document_spec, candidate)


def test_reference_context_accepts_documented_kdh_scale(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    make_pdf(pdf_path)
    candidate = observation()
    candidate.update(
        {
            "unit": "KDH",
            "scale": 1000,
            "reference_value": "100",
            "normalized_value": "100000",
        }
    )

    with pymupdf.open(pdf_path) as document:
        _validate_context(document, {"exercise_year": 2025}, candidate)


def test_verified_reference_becomes_the_only_admissible_value(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    source_sha = make_pdf(pdf_path)
    csv_path = tmp_path / "table.csv"
    csv_path.write_text("label,2025\nTotal actif,100\n", encoding="utf-8")
    integrated_path = tmp_path / "unified_document.json"
    integrated_path.write_text(
        json.dumps(
            {
                "source_pdf": str(pdf_path),
                "source_sha256": source_sha,
                "tables": [
                    {
                        "table_id": "DOCLING_001",
                        "source": "DOCLING",
                        "page": [1],
                        "reporting_scope": "UNKNOWN",
                        "unit": "UNKNOWN",
                        "csv_path": str(csv_path),
                        "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reference_path = tmp_path / "references.json"
    reference_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "documents": [
                    {
                        "company_id": "acme",
                        "issuer": "ACME",
                        "exercise_year": 2025,
                        "source_pdf": str(pdf_path),
                        "source_pdf_sha256": source_sha,
                        "integrated_document": str(integrated_path),
                        "document_evidence": {
                            "identity": {
                                "mode": "TEXT_SEARCH",
                                "page_number": 1,
                                "quote": "ACME",
                            },
                            "exercise": {
                                "mode": "TEXT_SEARCH",
                                "page_number": 1,
                                "quote": "2025",
                            },
                            "scope": {
                                "mode": "TEXT_SEARCH",
                                "page_number": 1,
                                "quote": "Comptes sociaux",
                            },
                            "unit": {
                                "mode": "TEXT_SEARCH",
                                "page_number": 1,
                                "quote": "En MAD",
                            },
                        },
                        "observations": [copy.deepcopy(observation())],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_reference_validation(reference_path, tmp_path / "quality")

    assert report["summary"] == {
        "sample_candidates_examined": 1,
        "reliable_references": 1,
        "verified": 1,
        "review_required": 0,
        "rejected": 0,
        "not_checked": 0,
        "data05_admissible": 1,
    }
    verified = report["concordance"][0]
    assert verified["verification_status"] == "VERIFIED"
    assert verified["normalized_value"] == "100"
    assert verified["downstream_eligible"] is True


@pytest.mark.skipif(
    not DEFAULT_REFERENCE_FILE.is_file(),
    reason="Références des pilotes annuels indisponibles",
)
def test_annual_pilot_reference_regression(tmp_path: Path) -> None:
    required = [
        PROJECT_ROOT
        / "data/integrated/ammc/S2M_RFA_2025_81084c026ed0/unified_document.json",
        PROJECT_ROOT
        / "data/integrated/ammc/Dari_Couspate_RFA_2023_e98a5ff007be/unified_document.json",
        PROJECT_ROOT
        / "data/integrated/ammc/AWB_RFA_2025_766da5ac174b/unified_document.json",
        PROJECT_ROOT
        / "data/integrated/ammc/Wafa_Assurance_RFA_2025_162f0a480d63/unified_document.json",
        PROJECT_ROOT
        / "data/integrated/ammc/Aradei_Capital_RFA_2025_272fdb3d9d6e/unified_document.json",
    ]
    if not all(path.is_file() for path in required):
        pytest.skip("Artefacts intégrés des pilotes indisponibles")

    report = run_reference_validation(DEFAULT_REFERENCE_FILE, tmp_path / "quality")

    assert report["candidate_universe_audited"] == 9101
    assert report["summary"] == {
        "sample_candidates_examined": 20,
        "reliable_references": 17,
        "verified": 16,
        "review_required": 3,
        "rejected": 1,
        "not_checked": 0,
        "data05_admissible": 16,
    }
    assert all(
        item["downstream_eligible"]
        == (item["verification_status"] == "VERIFIED")
        for item in report["concordance"]
    )
