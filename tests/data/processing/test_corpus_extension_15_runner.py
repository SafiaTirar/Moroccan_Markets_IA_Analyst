from moroccan_markets_ai.data.processing import corpus_extension_15_runner as runner
from moroccan_markets_ai.data.processing.corpus_extension_runner import (
    select_extension,
)


def audit_row(company_id: str, year: int) -> dict[str, str]:
    return {
        "company_id": company_id,
        "issuer": company_id,
        "exercise_year": str(year),
        "audit_status": "SUCCESS",
        "manifest_valid": "true",
        "document_classification": "TEXT",
        "page_count": "10",
        "actual_size_bytes": "1000",
        "sha256": "a" * 64,
        "local_path": f"data/{company_id}_{year}.pdf",
        "ocr_candidate_pages": "1",
        "financial_candidate_pages": "5",
        "estimated_workload_units": "10",
        "recommended_strategy": "DOCLING",
    }


def test_data_04_15_selects_ten_new_text_reports_in_order() -> None:
    rows = [audit_row(company, int(year)) for company, year in runner.SELECTION_KEYS]
    selected = select_extension(
        rows,
        rows,
        [{"company_id": "s2m", "exercise_year": 2025}],
        candidate_keys=runner.SELECTION_KEYS,
        reasons=runner.SELECTION_REASONS,
    )

    assert [
        (item["company_id"], item["exercise_year"]) for item in selected
    ] == runner.SELECTION_KEYS
    assert len(selected) == runner.MAX_DOCUMENTS == 10
    assert all(item["document_classification"] == "TEXT" for item in selected)
