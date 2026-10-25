import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.financial_quality_gate import (
    evaluate_quality,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTEGRATED_ROOT = PROJECT_ROOT / "data" / "integrated" / "ammc"


def integrated_document(name: str) -> Path:
    return INTEGRATED_ROOT / name / "unified_document.json"


def quarantine(report: dict) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(report["outputs"]["quarantine"]).read_text().splitlines()
    ]


RCI = integrated_document("RCI_Finance_Maroc_RFS_S1_26_111bfde57900")
SRM = integrated_document("SRM_S1_26_0_62925da1982b")
STROC = integrated_document("Stroc_industrie_S1_26_0_af7310ce4003")
S2M = integrated_document("S2M_RFS_S1_26_3de821c3b1d4")
DARI = integrated_document("Dari_Couspate_RFS_S1_26_d54a30399956")


@pytest.mark.skipif(not RCI.exists(), reason="Artefact intégré RCI indisponible")
def test_rci_quarantines_exactly_18_concatenated_amounts() -> None:
    report = evaluate_quality(RCI)
    values = quarantine(report)
    concatenated = [
        item
        for item in values
        if any(
            reason["code"] == "SUSPICIOUS_CONCATENATED_AMOUNT"
            for reason in item["reasons"]
        )
    ]

    assert len(concatenated) == 18
    assert all(not item["downstream_eligible"] for item in concatenated)
    assert all(item["provenance"]["bbox"] for item in concatenated)
    assert report["summary"]["admissible_values"] == 771
    assert report["summary"]["non_admissible_values"] == 26
    assert report["summary"]["by_status"] == {
        "VERIFIED": 1,
        "REVIEW_REQUIRED": 0,
        "REJECTED": 26,
        "NOT_CHECKED": 770,
    }
    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["rci_rdfm_result_net_prior"]["outcome"] == "PASS"


@pytest.mark.skipif(not SRM.exists(), reason="Artefact intégré SRM indisponible")
def test_srm_quarantines_three_unlabelled_rows() -> None:
    report = evaluate_quality(SRM)
    values = quarantine(report)
    unlabelled = [
        item
        for item in values
        if any(
            reason["code"] == "MISSING_ROW_LABEL"
            for reason in item["reasons"]
        )
    ]

    assert len(unlabelled) == 3
    assert len({(item["table_key"], item["row_index"]) for item in unlabelled}) == 3


@pytest.mark.skipif(not STROC.exists(), reason="Artefact intégré Stroc indisponible")
def test_stroc_remaining_ocr_anomalies_are_not_admissible() -> None:
    report = evaluate_quality(STROC)
    values = quarantine(report)
    codes = {
        reason["code"]
        for item in values
        for reason in item["reasons"]
    }

    assert report["status"] == "REVIEW_REQUIRED"
    assert codes >= {
        "DECIMAL_SEPARATOR_AMBIGUITY",
        "INCONSISTENT_DECIMAL_FORMAT",
        "NOISY_ROW_LABEL",
        "UNEXPECTED_EXTRA_COLUMN",
    }
    assert all(not item["downstream_eligible"] for item in values)
    checks = {item["check_id"]: item for item in report["checks"]}
    assert checks["stroc_net_result_cpc_vs_balance_current"]["outcome"] == "PASS"
    assert checks["stroc_net_result_current_formula"]["outcome"] == "PASS"


@pytest.mark.skipif(not SRM.exists(), reason="Artefact intégré SRM indisponible")
def test_srm_review_has_references_and_net_result_formula() -> None:
    report = evaluate_quality(SRM)
    checks = {item["check_id"]: item for item in report["checks"]}

    assert checks["srm_net_result_current_formula"]["outcome"] == "PASS"
    assert report["summary"]["by_status"]["VERIFIED"] == 4
    assert report["summary"]["by_status"]["REJECTED"] == 18


@pytest.mark.skipif(not S2M.exists(), reason="Artefact intégré S2M indisponible")
def test_s2m_preserves_clean_totals_without_fictitious_verification() -> None:
    report = evaluate_quality(S2M)
    annotations = [
        json.loads(line)
        for line in Path(report["outputs"]["annotations"]).read_text().splitlines()
    ]
    total = [
        item
        for item in annotations
        if item["raw_value"] == "317 569 112,54"
    ]

    assert total
    assert all(item["downstream_eligible"] for item in total)
    assert all(item["status"] == "NOT_CHECKED" for item in total)
    assert report["summary"]["by_status"]["VERIFIED"] == 0


@pytest.mark.parametrize("path", [RCI, SRM, STROC, S2M, DARI])
def test_available_reports_never_claim_global_verification(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"Artefact intégré indisponible : {path.parent.name}")

    report = evaluate_quality(path)

    assert report["status"] != "VERIFIED"
    assert report["financial_validation"] in {
        "NOT_PERFORMED",
        "PARTIAL_REFERENCE_CHECK",
    }
