from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from moroccan_markets_ai.data.processing import corpus_batch_runner as runner


def row(
    company: str,
    year: int,
    *,
    classification: str,
    workload: int,
    pages: int = 10,
) -> dict[str, str]:
    return {
        "company_id": company,
        "issuer": company.upper(),
        "exercise_year": str(year),
        "audit_status": "SUCCESS",
        "manifest_valid": "True",
        "document_classification": classification,
        "page_count": str(pages),
        "actual_size_bytes": "1000",
        "estimated_workload_units": str(workload),
        "ocr_candidate_pages": "2" if classification == "MIXED" else "",
        "financial_candidate_pages": "3",
        "recommended_strategy": "HYBRID" if classification == "MIXED" else "DOCLING",
        "local_path": f"data/{company}_{year}.pdf",
        "sha256": hashlib.sha256(f"{company}{year}".encode()).hexdigest(),
        "pdf_url": f"https://ammc.example/{company}/{year}.pdf",
        "detail_url": f"https://ammc.example/{company}/{year}",
    }


def test_selection_respects_comparison_history_and_new_company() -> None:
    candidates = [
        row("known_a", 2024, classification="MIXED", workload=20),
        row("known_b", 2024, classification="TEXT", workload=30),
        row("known_b", 2023, classification="TEXT", workload=10),
        row("new_a", 2025, classification="TEXT", workload=5),
        row("new_b", 2025, classification="TEXT", workload=50),
    ]
    coverage = [
        {"company_id": "known_a", "status": "VERIFIED"},
        {"company_id": "known_b", "status": "VERIFIED"},
    ]

    selected = runner.select_documents(
        candidates,
        candidates,
        coverage,
        {("known_a", "2025"), ("known_b", "2025")},
    )

    assert [(item["company_id"], item["exercise_year"]) for item in selected] == [
        ("known_a", "2024"),
        ("known_b", "2023"),
        ("new_a", "2025"),
    ]
    assert len(selected) == 3


def test_processed_document_is_not_selected() -> None:
    candidates = [
        row("known_a", 2024, classification="MIXED", workload=1),
        row("known_a", 2023, classification="MIXED", workload=20),
        row("known_b", 2024, classification="TEXT", workload=30),
        row("known_b", 2023, classification="TEXT", workload=10),
        row("new_a", 2025, classification="TEXT", workload=5),
    ]
    selected = runner.select_documents(
        candidates,
        candidates,
        [
            {"company_id": "known_a", "status": "VERIFIED"},
            {"company_id": "known_b", "status": "VERIFIED"},
        ],
        {("known_a", "2025"), ("known_a", "2024"), ("known_b", "2025")},
    )

    assert ("known_a", "2024") not in {
        (item["company_id"], item["exercise_year"]) for item in selected
    }


def test_preflight_recomputes_pdf_hash_size_and_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "report.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((50, 50), "Annual report 2025")
    document.save(pdf)
    document.close()
    sha256 = runner.file_hash(pdf)
    selection = {
        "company_id": "issuer",
        "issuer": "ISSUER",
        "exercise_year": "2025",
        "local_path": "report.pdf",
        "sha256": sha256,
        "actual_size_bytes": str(pdf.stat().st_size),
        "page_count": "1",
    }
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)

    manifest = [
        {
            "company_id": "issuer",
            "issuer": "ISSUER",
            "exercise_year": "2025",
            "sha256": sha256,
        }
    ]
    result = runner.validate_selection([selection], manifest)

    assert result[0]["status"] == "PASSED"
    selection["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        runner.validate_selection([selection], manifest)


def test_resume_uses_valid_cached_result_without_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = row("issuer", 2025, classification="TEXT", workload=1)
    cached = {
        "company_id": "issuer",
        "pipeline_status": "PARTIAL",
        "peak_rss_bytes": 100,
        "artifacts_sha256": {"artifact": "hash"},
        "metrics": {"tables_missing_page_reference": 0},
    }
    monkeypatch.setattr(runner, "reusable_result", lambda *_: cached)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("worker should not run"),
    )

    results, stop_reason = runner.execute_batch(
        [selection], resume=True, output_root=tmp_path
    )

    assert results == [cached]
    assert stop_reason is None


def test_ocr_failure_is_reported_but_only_grave_failures_stop() -> None:
    partial = {
        "pipeline_status": "PARTIAL",
        "peak_rss_bytes": 100,
        "artifacts_sha256": {"quality": "hash"},
        "stages": {"ocr": {"status": "FAILED"}},
        "metrics": {"tables_missing_page_reference": 0},
    }
    failed = {**partial, "pipeline_status": "FAILED"}

    assert runner.must_stop(partial) == (False, None)
    assert runner.must_stop(failed) == (True, "PIPELINE_FAILED")


def test_accelerated_batch_isolates_failed_document_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selections = [
        row("failed", 2025, classification="TEXT", workload=1),
        row("next", 2025, classification="TEXT", workload=2),
    ]
    cached = iter(
        [
            {
                "company_id": "failed",
                "exercise_year": 2025,
                "pipeline_status": "FAILED",
                "peak_rss_bytes": 100,
                "artifacts_sha256": {},
                "metrics": {"tables_missing_page_reference": 0},
            },
            {
                "company_id": "next",
                "exercise_year": 2025,
                "pipeline_status": "PARTIAL",
                "peak_rss_bytes": 100,
                "artifacts_sha256": {"quality": "hash"},
                "metrics": {"tables_missing_page_reference": 0},
            },
        ]
    )
    monkeypatch.setattr(runner, "reusable_result", lambda *_: next(cached))

    results, stop_reason = runner.execute_batch(
        selections,
        resume=True,
        output_root=tmp_path,
        continue_after_document_stop=True,
    )

    assert len(results) == 2
    assert results[0]["batch_control"]["reason"] == "PIPELINE_FAILED"
    assert results[1]["company_id"] == "next"
    assert stop_reason is None


def test_existing_processed_keys_come_from_reliable_reference_file(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "references.json"
    reference.write_text(
        json.dumps(
            {
                "documents": [
                    {"company_id": "issuer", "exercise_year": 2025}
                ]
            }
        ),
        encoding="utf-8",
    )

    assert runner.existing_processed_keys(reference) == {("issuer", "2025")}
