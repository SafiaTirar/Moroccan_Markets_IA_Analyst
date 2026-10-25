import pytest

from moroccan_markets_ai.data.processing.data_04_13_report import (
    add_version_metadata,
    build_review_dossiers,
    merge_coverage,
    strict_verified_export,
)


def test_merge_coverage_preserves_prior_versions() -> None:
    prior = [{"company_id": "a", "exercise_year": "2024", "accounting_perimeter": "CONSOLIDATED", "indicator": "REVENUE", "status": "VERIFIED"}]
    extension = [{"company_id": "b", "exercise_year": 2025, "accounting_perimeter": "CONSOLIDATED", "indicator": "REVENUE", "status": "VERIFIED"}]
    assert len(merge_coverage(prior, extension)) == 2


def test_strict_export_never_includes_review_required() -> None:
    report = {
        "concordance": [
            {
                "company_id": "a",
                "exercise_year": 2025,
                "indicator": "REVENUE",
                "normalized_value": "100",
                "currency": "MAD",
                "scale": 1,
                "accounting_perimeter": "CONSOLIDATED",
                "source_pdf_sha256": "a" * 64,
                "page_number": 1,
                "table_id": "DOCLING_001",
                "reference_id": "ref-1",
                "verification_status": "VERIFIED",
                "downstream_eligible": True,
            },
            {
                "verification_status": "REVIEW_REQUIRED",
                "downstream_eligible": False,
                "normalized_value": None,
            },
        ]
    }
    rows = strict_verified_export(report)
    assert len(rows) == 1
    assert rows[0]["verification_status"] == "VERIFIED"


def test_version_metadata_distinguishes_report_and_comparative_year() -> None:
    rows = [{"verification_reference_id": "ref-comparative"}]
    specification = {
        "documents": [
            {
                "exercise_year": 2025,
                "observations": [
                    {
                        "reference_id": "ref-comparative",
                        "period": "31/12/2024",
                        "source_label": "Chiffre d'affaires",
                    }
                ],
            }
        ]
    }
    enriched = add_version_metadata(rows, specification)
    assert enriched[0]["report_exercise_year"] == 2025
    assert enriched[0]["observation_year"] == 2024
    assert enriched[0]["period_role"] == "COMPARATIVE"


def test_review_dossier_keeps_source_value_and_no_fake_approval() -> None:
    candidates = [
        {
            "company_id": "issuer",
            "exercise_year": 2025,
            "status": "REVIEW_REQUIRED",
            "source_pdf": "/source.pdf",
            "page": [3],
            "table_id": "DOCLING_001",
            "row_index": 2,
            "label": "Résultat net",
            "column": "2025",
            "raw_value": "1 00?",
            "unit": None,
            "period": "2025",
            "reporting_scope": "UNKNOWN",
            "provenance": {"bbox": None},
            "reasons": [{"code": "AMBIGUOUS", "message": "Lecture ambiguë"}],
        }
    ]
    dossier = build_review_dossiers(candidates, 1)[0]
    assert dossier["raw_value"] == "1 00?"
    assert dossier["status"] == "REVIEW_REQUIRED"
    assert "Aucune validation humaine" in dossier["decision"]


def test_review_dossier_rejects_incomplete_document_universe() -> None:
    with pytest.raises(ValueError, match="incomplet"):
        build_review_dossiers([], 1)
