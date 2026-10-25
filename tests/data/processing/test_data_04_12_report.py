import pytest

from moroccan_markets_ai.data.processing.data_04_12_report import (
    merge_coverage,
    strict_verified_export,
)


def test_coverage_extension_preserves_history_and_replaces_same_cell() -> None:
    prior = [
        {
            "company_id": "issuer",
            "exercise_year": 2024,
            "accounting_perimeter": "SOCIAL",
            "indicator": "REVENUE",
            "status": "NOT_CHECKED",
        }
    ]
    extension = [
        {
            **prior[0],
            "status": "VERIFIED",
        },
        {
            "company_id": "new_issuer",
            "exercise_year": 2025,
            "accounting_perimeter": "CONSOLIDATED",
            "indicator": "TOTAL_ASSETS",
            "status": "VERIFIED",
        },
    ]

    merged = merge_coverage(prior, extension)

    assert len(merged) == 2
    assert sum(row["status"] == "VERIFIED" for row in merged) == 2


def test_strict_verified_export_excludes_every_other_status() -> None:
    report = {
        "concordance": [
            {
                "company_id": "issuer",
                "exercise_year": 2025,
                "indicator": "NET_INCOME",
                "normalized_value": "100",
                "currency": "MAD",
                "scale": 1,
                "accounting_perimeter": "SOCIAL",
                "source_pdf_sha256": "a" * 64,
                "page_number": 1,
                "table_id": "DOCLING_001",
                "reference_id": "verified-reference",
                "verification_status": "VERIFIED",
                "downstream_eligible": True,
            },
            {
                "company_id": "issuer",
                "exercise_year": 2025,
                "indicator": "REVENUE",
                "normalized_value": None,
                "currency": "MAD",
                "scale": 1,
                "accounting_perimeter": "SOCIAL",
                "source_pdf_sha256": "a" * 64,
                "page_number": 1,
                "table_id": "DOCLING_001",
                "reference_id": "review-reference",
                "verification_status": "REVIEW_REQUIRED",
                "downstream_eligible": False,
            },
        ]
    }

    exported = strict_verified_export(report)

    assert [row["verification_reference_id"] for row in exported] == [
        "verified-reference"
    ]
    assert all(row["verification_status"] == "VERIFIED" for row in exported)


def test_strict_verified_export_rejects_inconsistent_verified_record() -> None:
    report = {
        "concordance": [
            {
                "verification_status": "VERIFIED",
                "downstream_eligible": False,
                "normalized_value": "100",
            }
        ]
    }

    with pytest.raises(ValueError, match="downstream"):
        strict_verified_export(report)
