from moroccan_markets_ai.data.processing import corpus_extension_14_runner as runner
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


def test_data_04_14_selects_five_new_reports_in_declared_order() -> None:
    rows = [audit_row(company, int(year)) for company, year in runner.SELECTION_KEYS]
    processed = [{"company_id": "auto_hall", "exercise_year": 2024}]
    selected = select_extension(
        rows,
        rows,
        processed,
        candidate_keys=runner.SELECTION_KEYS,
        reasons=runner.SELECTION_REASONS,
    )
    assert [
        (item["company_id"], item["exercise_year"]) for item in selected
    ] == runner.SELECTION_KEYS
    assert len(selected) == 5
