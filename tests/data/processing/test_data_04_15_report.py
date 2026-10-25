import json

from moroccan_markets_ai.data.processing.data_04_15_report import (
    DEFAULT_REFERENCES,
    _document_key,
    _ocr_summary,
)


def test_ocr_summary_distinguishes_usable_and_empty_recovery() -> None:
    results = [
        {"stages": {"ocr": {"status": "PARSED"}}},
        {"stages": {"ocr": {"status": "FAILED"}}},
        {"stages": {"ocr": {"status": "NOT_RUN"}}},
        {"stages": {"ocr": {"status": "PARSED"}}},
    ]

    assert _ocr_summary(results) == {
        "parsed": 2,
        "failed_no_usable_table": 1,
        "not_run": 1,
    }


def test_data_04_15_reference_contract_is_strict() -> None:
    specification = json.loads(DEFAULT_REFERENCES.read_text(encoding="utf-8"))
    documents = specification["documents"]
    observations = [
        observation
        for document in documents
        for observation in document["observations"]
    ]

    assert len({_document_key(document) for document in documents}) == 10
    assert sum(item["expected_status"] == "VERIFIED" for item in observations) == 21
    assert sum(
        item["expected_status"] == "REVIEW_REQUIRED" for item in observations
    ) == 1
    assert all(
        item.get("reliable") is True
        for item in observations
        if item["expected_status"] == "VERIFIED"
    )
    unresolved = next(
        item for item in observations if item["expected_status"] == "REVIEW_REQUIRED"
    )
    assert unresolved["currency"] == "UNKNOWN"
    assert unresolved["scale"] is None
