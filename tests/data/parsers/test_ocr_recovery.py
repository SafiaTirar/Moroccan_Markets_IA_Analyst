import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from moroccan_markets_ai.data.parsers.ocr_recovery import (
    OCR_SCHEMA_VERSION,
    OcrConfig,
    RegionSelection,
    _amount,
    _canonical_amount,
    _load_cache,
    _score_tsv,
    _sha256,
    _validate_financial_tables,
)


def test_canonical_amount_repairs_ocr_spacing_and_decimal_separator():
    assert _canonical_amount("86845 499,75") == "86 845 499,75"
    assert _canonical_amount("1 347 720.64") == "1 347 720,64"
    assert _canonical_amount("- 1955 536,16") == "-1 955 536,16"
    assert _canonical_amount("-") == "-"


def test_amount_parses_french_financial_value():
    assert _amount("317 569 112,54") == Decimal("317569112.54")
    assert _amount("-") is None


def test_financial_validation_cross_checks_balance_totals():
    tables = {
        "bilan_actif_social": pd.DataFrame(
            [[
                "TOTAL GENERAL",
                "414 461 058,80",
                "86 845 499,75",
                "317 569 112,54",
                "297 032 298,41",
            ]] * 40,
            columns=["RUBRIQUE", "BRUT", "AMORT", "CURRENT", "PRIOR"],
        ),
        "bilan_passif_social": pd.DataFrame(
            [["TOTAL GENERAL", "317 569 112,54", "297 032 298,41"]] * 35,
            columns=["RUBRIQUE", "CURRENT", "PRIOR"],
        ),
        "cpc_social": pd.DataFrame(
            [["RESULTAT NET", "11 950 822,05", "11 198 861,84"]] * 30,
            columns=["NATURE", "CURRENT", "PRIOR"],
        ),
    }
    status, checks = _validate_financial_tables(tables)
    assert status == "PARSED"
    assert all(check["status"] == "PASS" for check in checks)


def test_financial_validation_rejects_unbalanced_totals():
    tables = {
        "bilan_actif_social": pd.DataFrame(
            [["TOTAL GENERAL", "414", "86", "1,00", "2,00"]] * 40,
            columns=["RUBRIQUE", "BRUT", "AMORT", "CURRENT", "PRIOR"],
        ),
        "bilan_passif_social": pd.DataFrame(
            [["TOTAL GENERAL", "9,00", "2,00"]] * 35,
            columns=["RUBRIQUE", "CURRENT", "PRIOR"],
        ),
        "cpc_social": pd.DataFrame(
            [["RESULTAT NET", "3,00", "4,00"]] * 30,
            columns=["NATURE", "CURRENT", "PRIOR"],
        ),
    }
    status, checks = _validate_financial_tables(tables)
    assert status == "PARTIAL"
    current = next(
        check
        for check in checks
        if check["name"] == "bilan_current_balanced"
    )
    assert current["status"] == "FAIL"


def test_generic_tsv_reconstruction_preserves_columns_and_coordinates():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    rows = ["1\t1\t0\t0\t0\t0\t0\t0\t600\t400\t-1\t"]
    labels = ["PRODUITS", "CHARGES", "RESULTAT"]
    for line, label in enumerate(labels, 1):
        rows.extend(
            [
                f"5\t1\t1\t1\t{line}\t1\t20\t{line * 50}\t120\t20\t95\t{label}",
                f"5\t1\t1\t1\t{line}\t2\t300\t{line * 50}\t30\t20\t95\t1",
                f"5\t1\t1\t1\t{line}\t3\t334\t{line * 50}\t70\t20\t95\t234,56",
                f"5\t1\t1\t1\t{line}\t4\t470\t{line * 50}\t30\t20\t95\t2",
                f"5\t1\t1\t1\t{line}\t5\t504\t{line * 50}\t70\t20\t95\t345,67",
            ]
        )
    region = RegionSelection(
        page_no=2,
        bbox=(100, 200, 700, 600),
        source="configured_region",
        source_id="test",
    )

    scored = _score_tsv("\n".join([header, *rows]), region, OcrConfig())
    dataframe = scored["dataframe"]

    assert list(dataframe.columns) == ["label", "value_1", "value_2"]
    assert dataframe.iloc[0].tolist() == [
        "PRODUITS",
        "1 234,56",
        "2 345,67",
    ]
    first_bbox = scored["details"]["rows"][0]["cells"][0]["bbox"]
    assert first_bbox[0] >= 100
    assert first_bbox[1] >= 200


def test_generic_tsv_reconstruction_accepts_grouped_integer_amounts():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t600\t400\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t20\t50\t120\t20\t95\tTOTAL\n"
        "5\t1\t1\t1\t1\t2\t300\t50\t20\t20\t95\t11\n"
        "5\t1\t1\t1\t1\t3\t324\t50\t35\t20\t95\t495\n"
        "5\t1\t1\t1\t1\t4\t363\t50\t35\t20\t95\t053\n"
        "5\t1\t1\t1\t1\t5\t402\t50\t35\t20\t95\t869"
    )
    region = RegionSelection(
        page_no=1,
        bbox=(0, 0, 600, 400),
        source="configured_region",
        source_id="test",
    )

    scored = _score_tsv(tsv, region, OcrConfig())

    assert scored["dataframe"].iloc[0, 1] == "11 495 053 869"


def test_configuration_fingerprint_changes_with_ocr_strategy():
    assert OcrConfig().fingerprint != OcrConfig(psms=(6,)).fingerprint


def test_ocr_cache_checks_configuration_and_artifact_hashes(
    tmp_path: Path,
):
    config = OcrConfig()
    artifact = tmp_path / "tables" / "table.csv"
    artifact.parent.mkdir()
    artifact.write_text("label,value\nRésultat,10\n", encoding="utf-8")
    metadata = tmp_path / "ocr_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": OCR_SCHEMA_VERSION,
                "source_sha256": "source-sha",
                "config_sha256": config.fingerprint,
                "artifact_sha256": {
                    "tables/table.csv": _sha256(artifact),
                },
            }
        ),
        encoding="utf-8",
    )

    assert _load_cache(metadata, "source-sha", config)["cache_used"]

    artifact.write_text("corrompu\n", encoding="utf-8")
    assert _load_cache(metadata, "source-sha", config) is None


def test_ocr_cache_accepts_documented_negative_result(tmp_path: Path):
    config = OcrConfig()
    metadata = tmp_path / "ocr_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": OCR_SCHEMA_VERSION,
                "source_sha256": "source-sha",
                "config_sha256": config.fingerprint,
                "status": "FAILED",
                "tables_detected": 0,
                "regions_examined": 1,
                "rejected_regions": [
                    {
                        "page": 2,
                        "reason": "NOT_FINANCIAL_TABLE",
                        "attempts": [{"psm": 6, "score": 0}],
                    }
                ],
                "artifact_sha256": {},
            }
        ),
        encoding="utf-8",
    )

    cached = _load_cache(metadata, "source-sha", config)

    assert cached is not None
    assert cached["cache_used"] is True


def test_ocr_cache_rejects_undocumented_empty_result(tmp_path: Path):
    config = OcrConfig()
    metadata = tmp_path / "ocr_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": OCR_SCHEMA_VERSION,
                "source_sha256": "source-sha",
                "config_sha256": config.fingerprint,
                "status": "FAILED",
                "tables_detected": 0,
                "regions_examined": 0,
                "rejected_regions": [],
                "artifact_sha256": {},
            }
        ),
        encoding="utf-8",
    )

    assert _load_cache(metadata, "source-sha", config) is None


def test_ocr_cache_checks_page_and_region_selection(tmp_path: Path):
    config = OcrConfig()
    artifact = tmp_path / "tables" / "table.csv"
    artifact.parent.mkdir()
    artifact.write_text("label,value\nRésultat,10\n", encoding="utf-8")
    selection = {
        "selected_pages": [2, 3],
        "regions_json_sha256": None,
    }
    selection_sha = hashlib.sha256(
        json.dumps(selection, sort_keys=True).encode()
    ).hexdigest()
    metadata = tmp_path / "ocr_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": OCR_SCHEMA_VERSION,
                "source_sha256": "source-sha",
                "config_sha256": config.fingerprint,
                "selection_sha256": selection_sha,
                "artifact_sha256": {
                    "tables/table.csv": _sha256(artifact),
                },
            }
        ),
        encoding="utf-8",
    )

    assert _load_cache(
        metadata,
        "source-sha",
        config,
        selection=selection,
    )
    changed = {**selection, "selected_pages": [2]}
    assert (
        _load_cache(
            metadata,
            "source-sha",
            config,
            selection=changed,
        )
        is None
    )
