from moroccan_markets_ai.data.processing import corpus_extension_16_runner as runner
from moroccan_markets_ai.data.processing.corpus_extension_runner import (
    select_extension,
)


def audit_row(company_id: str, year: int, classification: str) -> dict[str, str]:
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
        "estimated_workload_units": "10",
        "recommended_strategy": "HYBRID" if classification == "MIXED" else "DOCLING",
    }


def test_data_04_16_selection_preserves_text_then_mixed_order() -> None:
    rows = [
        audit_row(company, int(year), "TEXT" if index < 7 else "MIXED")
        for index, (company, year) in enumerate(runner.SELECTION_KEYS)
    ]
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
    assert [item["document_classification"] for item in selected[:7]] == [
        "TEXT"
    ] * 7
    assert [item["document_classification"] for item in selected[7:]] == [
        "MIXED"
    ] * 3


def test_mixed_gate_requires_every_text_result_to_be_safe() -> None:
    safe = [{"pipeline_status": "PARTIAL"} for _ in runner.TEXT_KEYS]
    assert runner.sublot_is_safe(safe, 7)

    failed = [*safe[:-1], {"pipeline_status": "FAILED"}]
    assert not runner.sublot_is_safe(failed, 7)

    memory_stop = [
        *safe[:-1],
        {
            "pipeline_status": "PARTIAL",
            "batch_control": {"document_stopped": True},
        },
    ]
    assert not runner.sublot_is_safe(memory_stop, 7)
