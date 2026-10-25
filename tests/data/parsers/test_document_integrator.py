
import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.document_integrator import (
    file_hash,
    integrate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DOCLING_METADATA = (
    PROJECT_ROOT
    / "data/extracted/ammc"
    / "S2M_RFS_S1_26_3de821c3b1d4"
    / "metadata.json"
)

OCR_METADATA = (
    PROJECT_ROOT
    / "data/extracted/ammc/ocr"
    / "S2M_RFS_S1_26"
    / "targeted_financial_tables"
    / "ocr_metadata.json"
)


@pytest.mark.skipif(
    not DOCLING_METADATA.exists() or not OCR_METADATA.exists(),
    reason="Artefacts locaux S2M indisponibles",
)
def test_integrate_s2m(tmp_path):
    result = integrate(
        docling_metadata=DOCLING_METADATA,
        ocr_metadata=OCR_METADATA,
        output_root=tmp_path,
    )

    assert result["active_table_count"] == 14
    assert result["integration_status"] == "PARTIAL"

    table_ids = {
        table["table_id"]
        for table in result["tables"]
    }

    assert "DOCLING_007" not in table_ids
    assert "DOCLING_008" not in table_ids

    assert "OCR_bilan_actif_social" in table_ids
    assert "OCR_bilan_passif_social" in table_ids
    assert "OCR_cpc_social" in table_ids

    assert len(table_ids) == len(result["tables"])
    assert len(result["replacements"]) == 3


def test_integrate_document_without_ocr(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-synthetic")
    extraction = tmp_path / "docling"
    tables = extraction / "tables"
    tables.mkdir(parents=True)
    (tables / "table_001.csv").write_text(
        "label,value\nRésultat,10\n",
        encoding="utf-8",
    )
    metadata = extraction / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "source_pdf": str(source),
                "sha256": file_hash(source),
                "status": "PARSED",
                "validation": {
                    "tables": [
                        {
                            "table_index": 1,
                            "pages": [1],
                            "status": "VALID",
                        }
                    ],
                    "issues": [],
                },
            }
        ),
        encoding="utf-8",
    )

    result = integrate(
        docling_metadata=metadata,
        output_root=tmp_path / "integrated",
    )

    assert result["integration_status"] == "PARSED"
    assert result["ocr_status"] == "NOT_RUN"
    assert result["active_table_count"] == 1


def test_integrate_ocr_only_and_remove_content_duplicates(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-synthetic")
    extraction = tmp_path / "ocr"
    tables = extraction / "tables"
    tables.mkdir(parents=True)
    csv_path = tables / "table.csv"
    csv_path.write_text("label,value\nRésultat,10\n", encoding="utf-8")
    table = {
        "table_id": "OCR_001",
        "page": 2,
        "rows": 1,
        "columns": 2,
        "csv": "tables/table.csv",
        "quality_status": "TECHNICALLY_RECOVERED",
    }
    metadata = extraction / "ocr_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "source_pdf": str(source),
                "source_sha256": file_hash(source),
                "status": "PARSED",
                "tables": [table, {**table, "table_id": "OCR_002"}],
            }
        ),
        encoding="utf-8",
    )

    result = integrate(
        ocr_metadata=metadata,
        output_root=tmp_path / "integrated",
    )

    assert result["docling_status"] == "NOT_RUN"
    assert result["integration_status"] == "PARTIAL"
    assert result["active_table_count"] == 1
    assert result["financial_validation"] == "NOT_PERFORMED"


def test_integrate_ocr_uses_table_id_when_name_is_null(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-synthetic")
    extraction = tmp_path / "ocr"
    tables = extraction / "tables"
    tables.mkdir(parents=True)
    csv_path = tables / "table.csv"
    csv_path.write_text("label,value\nRésultat,10\n", encoding="utf-8")
    metadata = extraction / "ocr_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "source_pdf": str(source),
                "source_sha256": file_hash(source),
                "status": "PARSED",
                "tables": [
                    {
                        "table_id": "OCR_001",
                        "name": None,
                        "page": 1,
                        "rows": 1,
                        "columns": 2,
                        "csv": "tables/table.csv",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = integrate(
        ocr_metadata=metadata,
        output_root=tmp_path / "integrated",
    )

    assert result["tables"][0]["table_id"] == "OCR_001"
