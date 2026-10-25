import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.processing.financial_multisector_report import (
    build_coverage_matrix,
    processing_must_stop,
    verified_dataset,
)


def _quality_report(tmp_path: Path, annotations: list[dict]) -> Path:
    annotations_path = tmp_path / "annotations.jsonl"
    annotations_path.write_text(
        "".join(json.dumps(item) + "\n" for item in annotations),
        encoding="utf-8",
    )
    report_path = tmp_path / "quality.json"
    report_path.write_text(
        json.dumps({"outputs": {"annotations": str(annotations_path)}}),
        encoding="utf-8",
    )
    return report_path


def test_coverage_keeps_sector_and_accounting_perimeter_separate(
    tmp_path: Path,
) -> None:
    quality_path = _quality_report(
        tmp_path,
        [
            {
                "label": "Produit net bancaire",
                "reporting_scope": "UNKNOWN",
            }
        ],
    )
    registry = {
        "company_profiles": {"bank": "BANKING"},
        "indicators": [
            {
                "indicator": "BANKING_NET_REVENUE",
                "business_definition": "PNB",
                "synonyms": ["produit net bancaire"],
                "applicable_profiles": ["BANKING"],
            },
            {
                "indicator": "REVENUE",
                "business_definition": "CA industriel",
                "synonyms": ["chiffre d'affaires"],
                "applicable_profiles": ["GENERAL_CORPORATE"],
            },
        ],
    }
    report = {
        "documents": [
            {
                "company_id": "bank",
                "exercise_year": 2025,
                "quality_report": str(quality_path),
            }
        ],
        "concordance": [
            {
                "company_id": "bank",
                "exercise_year": 2025,
                "indicator": "BANKING_NET_REVENUE",
                "accounting_perimeter": "CONSOLIDATED",
                "verification_status": "VERIFIED",
                "reference_id": "bank-pnb",
            }
        ],
    }

    rows = build_coverage_matrix(report, registry)
    index = {item["indicator"]: item for item in rows}

    assert index["BANKING_NET_REVENUE"]["status"] == "VERIFIED"
    assert index["BANKING_NET_REVENUE"]["accounting_perimeter"] == "CONSOLIDATED"
    assert index["REVENUE"]["status"] == "NOT_APPLICABLE"


def test_verified_export_never_leaks_review_or_rejected_values() -> None:
    base = {
        "company_id": "issuer",
        "exercise_year": 2025,
        "indicator": "NET_INCOME",
        "normalized_value": "100",
        "currency": "MAD",
        "scale": 1,
        "accounting_perimeter": "SOCIAL",
        "source_pdf_sha256": "abc",
        "page_number": 1,
        "table_id": "T1",
        "reference_id": "ref",
    }
    report = {
        "concordance": [
            {
                **base,
                "verification_status": "VERIFIED",
                "downstream_eligible": True,
            },
            {
                **base,
                "reference_id": "review",
                "verification_status": "REVIEW_REQUIRED",
                "downstream_eligible": False,
            },
            {
                **base,
                "reference_id": "rejected",
                "verification_status": "REJECTED",
                "downstream_eligible": False,
            },
        ]
    }

    exported = verified_dataset(report)

    assert [item["verification_reference_id"] for item in exported] == ["ref"]
    assert {item["verification_status"] for item in exported} == {"VERIFIED"}


def test_invalid_verified_record_is_rejected() -> None:
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
        verified_dataset(report)


@pytest.mark.parametrize(
    "result",
    [
        {"pipeline_status": "FAILED", "peak_rss_bytes": 1, "artifacts_sha256": {"a": "b"}},
        {"pipeline_status": "PARTIAL", "peak_rss_bytes": 101, "artifacts_sha256": {"a": "b"}},
        {"pipeline_status": "PARTIAL", "peak_rss_bytes": 1, "artifacts_sha256": {}},
    ],
)
def test_processing_stop_policy_covers_failure_memory_and_provenance(
    result: dict,
) -> None:
    assert processing_must_stop([result], max_peak_rss_bytes=100)
