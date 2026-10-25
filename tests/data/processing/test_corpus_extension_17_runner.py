from moroccan_markets_ai.data.processing import corpus_extension_17_runner as runner
from moroccan_markets_ai.data.processing.corpus_pipeline_pilot import (
    extraction_strategy,
    pipeline_fingerprint,
)


def audit_row(company_id: str, year: str, classification: str) -> dict[str, str]:
    return {
        "company_id": company_id,
        "issuer": company_id,
        "exercise_year": year,
        "audit_status": "SUCCESS",
        "manifest_valid": "true",
        "document_classification": classification,
        "page_count": "10",
        "actual_size_bytes": "1000",
        "sha256": "a" * 64,
        "local_path": f"data/{company_id}_{year}.pdf",
        "ocr_candidate_pages": "1;2",
        "financial_candidate_pages": "5",
        "estimated_workload_units": "10",
        "recommended_strategy": "OCR" if classification == "IMAGE" else "HYBRID",
    }


def test_image_strategy_is_ocr_first_and_part_of_cache_fingerprint() -> None:
    base = {
        "sha256": "a" * 64,
        "ocr_candidate_pages": "1;2",
        "document_classification": "IMAGE",
        "recommended_strategy": "OCR",
    }
    hybrid = {**base, "document_classification": "MIXED", "recommended_strategy": "HYBRID"}

    assert extraction_strategy(base) == "OCR_FIRST"
    assert extraction_strategy(hybrid) == "DOCLING_FIRST"
    assert pipeline_fingerprint(base) != pipeline_fingerprint(hybrid)


def test_image_gate_requires_ocr_quality_provenance_and_memory_margin() -> None:
    result = {
        "pipeline_status": "PARTIAL",
        "extraction_strategy": "OCR_FIRST",
        "peak_rss_bytes": 1024,
        "artifacts_sha256": {"artifact": "hash"},
        "metrics": {
            "pages_failed": 0,
            "ocr_pages_failed": 0,
            "ocr_tables_non_empty": 2,
            "integrated_active_tables": 2,
            "tables_missing_page_reference": 0,
        },
    }
    assert runner.image_pilot_assessment(result)["passed"]

    result["metrics"]["ocr_tables_non_empty"] = 0
    assessment = runner.image_pilot_assessment(result)
    assert not assessment["passed"]
    assert "ocr_table_recovered" in assessment["failed_checks"]


def test_image_gate_rejects_failed_or_memory_stopped_document() -> None:
    assessment = runner.image_pilot_assessment(
        {
            "pipeline_status": "FAILED",
            "extraction_strategy": "OCR_FIRST",
            "peak_rss_bytes": runner.MAX_SAFE_RSS_BYTES,
            "artifacts_sha256": {},
            "metrics": {},
        }
    )
    assert not assessment["passed"]
    assert "pipeline_not_failed" in assessment["failed_checks"]
    assert "memory_below_guard" in assessment["failed_checks"]


def test_data_04_17_selection_allows_only_explicit_mixed_and_image_set() -> None:
    rows = [
        audit_row(company, year, "MIXED" if index < 5 else "IMAGE")
        for index, (company, year) in enumerate(runner.SELECTION_KEYS)
    ]
    selected = runner.select_extension(
        rows,
        rows,
        [{"company_id": "prior", "exercise_year": 2025}],
        candidate_keys=runner.SELECTION_KEYS,
        reasons=runner.SELECTION_REASONS,
        allowed_classifications=frozenset({"MIXED", "IMAGE"}),
    )

    assert [item["document_classification"] for item in selected] == [
        "MIXED",
        "MIXED",
        "MIXED",
        "MIXED",
        "MIXED",
        "IMAGE",
        "IMAGE",
    ]
