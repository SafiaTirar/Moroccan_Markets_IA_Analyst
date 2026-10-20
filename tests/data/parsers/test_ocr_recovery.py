from decimal import Decimal

import pandas as pd

from moroccan_markets_ai.data.parsers.ocr_recovery import (
    _amount,
    _canonical_amount,
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
