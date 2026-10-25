import json

from moroccan_markets_ai.data.processing.data_04_16_report import (
    DEFAULT_REFERENCES,
    _class_metrics,
    _document_key,
)


def result(classification: str, pages: int) -> dict:
    return {
        "audit_classification": classification,
        "elapsed_seconds": 2.5,
        "peak_rss_bytes": 100,
        "metrics": {
            "pages_processed": pages,
            "docling_tables_detected": 4,
            "docling_tables_non_empty": 3,
            "ocr_pages_processed": 2,
            "ocr_tables_non_empty": 1,
            "ambiguous_pages": [1, 2],
            "quality_values_total": 9,
        },
    }


def test_class_metrics_separates_text_and_mixed() -> None:
    rows = [result("TEXT", 10), result("TEXT", 20), result("MIXED", 5)]

    assert _class_metrics(rows, "TEXT") == {
        "documents": 2,
        "pages": 30,
        "elapsed_seconds": 5.0,
        "peak_rss_bytes": 100,
        "docling_tables": 8,
        "docling_non_empty": 6,
        "ocr_pages": 4,
        "ocr_tables": 2,
        "ambiguous_pages": 4,
        "candidate_values": 18,
    }
    assert _class_metrics(rows, "MIXED")["documents"] == 1


def test_data_04_16_references_never_promote_stroc_ocr() -> None:
    specification = json.loads(DEFAULT_REFERENCES.read_text(encoding="utf-8"))
    documents = specification["documents"]
    observations = [
        observation
        for document in documents
        for observation in document["observations"]
    ]

    assert len({_document_key(document) for document in documents}) == 7
    assert all(document["company_id"] != "stroc" for document in documents)
    assert sum(item["expected_status"] == "VERIFIED" for item in observations) == 13
    assert sum(item["expected_status"] == "REJECTED" for item in observations) == 1
