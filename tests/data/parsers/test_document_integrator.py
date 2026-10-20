
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.document_integrator import (
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
    assert len(result["replacements"]) == 2
