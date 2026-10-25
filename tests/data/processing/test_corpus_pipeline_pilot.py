import hashlib
from pathlib import Path

import pymupdf

from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import (
    build_report,
    detect_reporting_scope,
    recommendation,
    select_pilots,
)


def audit_row(company: str, year: int, classification: str, size: int) -> dict[str, str]:
    return {
        "company_id": company,
        "issuer": company.upper(),
        "exercise_year": str(year),
        "document_classification": classification,
        "actual_size_bytes": str(size),
        "page_count": "10",
        "sha256": hashlib.sha256(company.encode()).hexdigest(),
        "local_path": f"data/{company}.pdf",
        "pdf_url": f"https://www.ammc.ma/{company}.pdf",
        "detail_url": f"https://www.ammc.ma/{company}",
    }


def plan_row(
    company: str, year: int, classification: str, workload: int
) -> dict[str, str]:
    return {
        "company_id": company,
        "exercise_year": str(year),
        "document_classification": classification,
        "estimated_workload_units": str(workload),
        "audit_status": "SUCCESS",
        "manifest_valid": "True",
        "ocr_candidate_pages": "2;4" if classification == "MIXED" else "3",
    }


def test_selection_is_lightweight_and_prefers_company_diversity() -> None:
    audits = [
        audit_row("alpha", 2025, "TEXT", 1024**2),
        audit_row("alpha", 2024, "MIXED", 1024**2),
        audit_row("beta", 2024, "MIXED", 2 * 1024**2),
    ]
    plans = [
        plan_row("alpha", 2025, "TEXT", 10),
        plan_row("alpha", 2024, "MIXED", 10),
        plan_row("beta", 2024, "MIXED", 20),
    ]

    selected = select_pilots(audits, plans)

    assert [(row["company_id"], row["document_classification"]) for row in selected] == [
        ("alpha", "TEXT"),
        ("beta", "MIXED"),
    ]


def test_scope_evidence_preserves_page_and_does_not_force_one_scope(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "scope.pdf"
    document = pymupdf.open()
    social = document.new_page()
    social.insert_text((50, 50), "Etats financiers sociaux")
    consolidated = document.new_page()
    consolidated.insert_text((50, 50), "Comptes consolides du groupe")
    document.save(pdf)
    document.close()

    scope = detect_reporting_scope(pdf)

    assert scope["classification"] == "MIXED"
    assert scope["confidence"] == "HIGH"
    assert {item["page"] for item in scope["evidence"]} == {1, 2}
    assert {item["scope"] for item in scope["evidence"]} == {
        "SOCIAL",
        "CONSOLIDATED",
    }


def test_scope_detects_both_meanings_in_combined_accounts_phrase(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "combined.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), "Audit des comptes individuels et consolides")
    document.save(pdf)
    document.close()

    scope = detect_reporting_scope(pdf)

    assert scope["classification"] == "MIXED"
    assert {item["scope"] for item in scope["evidence"]} == {
        "SOCIAL",
        "CONSOLIDATED",
    }


def test_recommendation_requires_reservations_for_unverified_data() -> None:
    results = [
        {
            "pipeline_status": "PARTIAL",
            "peak_rss_bytes": 1024,
            "elapsed_seconds": 1,
            "metrics": {
                "ambiguous_pages": [2],
                "data05_admissible_values": 0,
                "tables_missing_page_reference": 0,
                "pages_processed": 10,
                "ocr_pages_processed": 1,
                "docling_tables_detected": 2,
                "ocr_tables_detected": 1,
            },
        },
        {
            "pipeline_status": "SUCCESS",
            "peak_rss_bytes": 2048,
            "elapsed_seconds": 1,
            "metrics": {
                "ambiguous_pages": [],
                "data05_admissible_values": 0,
                "tables_missing_page_reference": 0,
                "pages_processed": 10,
                "ocr_pages_processed": 1,
                "docling_tables_detected": 2,
                "ocr_tables_detected": 1,
            },
        },
    ]

    status, reasons = recommendation(results)

    assert status == "GO AVEC RÉSERVES"
    assert len(reasons) == 3


def test_comparison_report_never_claims_financial_validation() -> None:
    selections = [
        {
            **audit_row("alpha", 2025, "TEXT", 1024),
            **plan_row("alpha", 2025, "TEXT", 10),
            "selection_score": 12,
        },
        {
            **audit_row("beta", 2024, "MIXED", 2048),
            **plan_row("beta", 2024, "MIXED", 20),
            "selection_score": 22,
        },
    ]
    results = [
        {
            "pipeline_status": "SUCCESS",
            "peak_rss_bytes": 1024,
            "elapsed_seconds": 1,
            "metrics": {
                "ambiguous_pages": [],
                "data05_admissible_values": 1,
                "tables_missing_page_reference": 0,
                "pages_processed": 10,
                "ocr_pages_processed": 1,
                "docling_tables_detected": 2,
                "ocr_tables_detected": 1,
            },
        }
        for _ in range(2)
    ]

    report = build_report(selections, results)

    assert report["recommendation"] == "GO"
    assert "n'est pas financièrement validé" in report[
        "financial_validation_warning"
    ]
    assert report["execution_policy"]["max_parallel_documents"] == 1
