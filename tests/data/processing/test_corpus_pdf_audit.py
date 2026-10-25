import csv
import hashlib
import json
from pathlib import Path

import pymupdf

from moroccan_markets_ai.data.processing.corpus_pdf_audit import (
    AuditConfig,
    audit_pdf,
    page_classification,
    run_audit,
)


def pdf_bytes(kind: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=200, height=300)
    if kind in {"text", "mixed"}:
        text = (
            "Etats financiers bilan actif passif resultat net. "
            "Ce texte est extractible et suffisamment long pour le controle. "
        )
        page.insert_textbox(pymupdf.Rect(10, 10, 190, 95), text * 3, fontsize=8)
    if kind in {"image", "mixed"}:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 0)
        pixmap.clear_with(0xA0A0A0)
        rectangle = (
            pymupdf.Rect(0, 0, 200, 300)
            if kind == "image"
            else pymupdf.Rect(10, 110, 190, 290)
        )
        page.insert_image(rectangle, pixmap=pixmap)
    content = document.tobytes()
    document.close()
    return content


def manifest_row(
    project_root: Path,
    *,
    company_id: str,
    year: int,
    relative_path: str,
    expected_content: bytes,
) -> dict[str, str]:
    return {
        "company_id": company_id,
        "issuer": company_id.upper(),
        "sector": "Test",
        "exercise_year": str(year),
        "report_type": "Rapports annuels",
        "report_family": "ANNUAL",
        "perimeter": "UNKNOWN",
        "detail_url": f"https://www.ammc.ma/fr/{company_id}-{year}",
        "pdf_url": f"https://www.ammc.ma/files/{company_id}-{year}.pdf",
        "local_path": relative_path,
        "sha256": hashlib.sha256(expected_content).hexdigest(),
        "size_bytes": str(len(expected_content)),
    }


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_page_threshold_classification_is_explicit() -> None:
    config = AuditConfig()

    assert page_classification(300, 0.01, config) == "TEXT"
    assert page_classification(0, 0.90, config) == "IMAGE"
    assert page_classification(300, 0.90, config) == "MIXED"
    assert page_classification(3, 0.01, config) == "UNKNOWN"


def test_synthetic_text_image_and_mixed_pages(tmp_path: Path) -> None:
    config = AuditConfig()
    expected = {"text": "TEXT", "image": "IMAGE", "mixed": "MIXED"}
    for kind, classification in expected.items():
        path = tmp_path / f"{kind}.pdf"
        path.write_bytes(pdf_bytes(kind))
        row = manifest_row(
            tmp_path,
            company_id=kind,
            year=2023,
            relative_path=path.name,
            expected_content=path.read_bytes(),
        )

        summary, pages = audit_pdf(
            path, row, config=config, config_sha=config.signature()
        )

        assert summary["audit_status"] == "SUCCESS"
        assert pages[0]["page_classification"] == classification
        if kind == "text":
            assert summary["financial_candidate_pages"] == "1"
        if kind == "image":
            assert pages[0]["ocr_candidate"] is True
            assert pages[0]["image_count"] == 1
            assert pages[0]["image_area_ratio"] == 1.0


def test_blank_page_is_unknown_but_not_an_ocr_candidate(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    path.write_bytes(pdf_bytes("blank"))
    config = AuditConfig()
    row = manifest_row(
        tmp_path,
        company_id="blank",
        year=2023,
        relative_path=path.name,
        expected_content=path.read_bytes(),
    )

    summary, pages = audit_pdf(
        path, row, config=config, config_sha=config.signature()
    )

    assert pages[0]["page_classification"] == "UNKNOWN"
    assert pages[0]["ocr_candidate"] is False
    assert summary["ocr_candidate_count"] == 0


def test_missing_file_and_wrong_sha_do_not_stop_other_documents(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    valid_content = pdf_bytes("text")
    invalid_sha_content = pdf_bytes("image")
    (pdf_dir / "valid.pdf").write_bytes(valid_content)
    (pdf_dir / "bad-sha.pdf").write_bytes(invalid_sha_content)
    rows = [
        manifest_row(
            tmp_path,
            company_id="valid",
            year=2023,
            relative_path="pdfs/valid.pdf",
            expected_content=valid_content,
        ),
        manifest_row(
            tmp_path,
            company_id="missing",
            year=2024,
            relative_path="pdfs/missing.pdf",
            expected_content=pdf_bytes("text"),
        ),
        manifest_row(
            tmp_path,
            company_id="bad_sha",
            year=2025,
            relative_path="pdfs/bad-sha.pdf",
            expected_content=b"different content",
        ),
    ]
    manifest = tmp_path / "manifest.csv"
    output_dir = tmp_path / "audit"
    write_manifest(manifest, rows)

    report = run_audit(
        manifest_path=manifest,
        output_dir=output_dir,
        project_root=tmp_path,
    )

    assert report["coverage"]["successful"] == 2
    assert report["coverage"]["failed"] == 1
    assert report["coverage"]["manifest_invalid"] == 2
    assert report["pages_by_classification"]["TEXT"] == 1
    assert report["total_image_placements"] == 1
    audits = list(csv.DictReader((output_dir / "annual_pdf_audit.csv").open()))
    by_company = {row["company_id"]: row for row in audits}
    assert by_company["valid"]["sha256_matches"] == "True"
    assert "FILE_MISSING" in by_company["missing"]["manifest_issues"]
    assert "SHA256_MISMATCH" in by_company["bad_sha"]["manifest_issues"]
    plans = list(csv.DictReader((output_dir / "annual_processing_plan.csv").open()))
    strategies = {row["company_id"]: row["recommended_strategy"] for row in plans}
    assert strategies["bad_sha"] == "MANUAL_REVIEW"
    assert json.loads((output_dir / "annual_pdf_audit_report.json").read_text())[
        "ocr_used"
    ] is False


def test_resume_caches_success_and_retries_previous_failure(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    first_content = pdf_bytes("text")
    late_content = pdf_bytes("image")
    (pdf_dir / "first.pdf").write_bytes(first_content)
    rows = [
        manifest_row(
            tmp_path,
            company_id="first",
            year=2023,
            relative_path="pdfs/first.pdf",
            expected_content=first_content,
        ),
        manifest_row(
            tmp_path,
            company_id="late",
            year=2024,
            relative_path="pdfs/late.pdf",
            expected_content=late_content,
        ),
    ]
    manifest = tmp_path / "manifest.csv"
    output_dir = tmp_path / "audit"
    write_manifest(manifest, rows)
    first = run_audit(
        manifest_path=manifest,
        output_dir=output_dir,
        project_root=tmp_path,
    )
    assert first["coverage"]["failed"] == 1

    (pdf_dir / "late.pdf").write_bytes(late_content)
    second = run_audit(
        manifest_path=manifest,
        output_dir=output_dir,
        project_root=tmp_path,
    )

    assert second["coverage"]["successful"] == 2
    assert second["coverage"]["failed"] == 0
    assert second["coverage"]["cached_documents"] == 1
