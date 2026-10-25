import copy
import json
from pathlib import Path

from moroccan_markets_ai.data.parsers.document_integrator import read_json
from moroccan_markets_ai.data.processing.financial_validation_extension import (
    DEFAULT_REGISTRY,
    _indicator_match,
    build_priority_queue,
    run_deterministic_checks,
    validate_indicator_registry,
)


def _annotation(value_id: str, raw_value: str = "100") -> dict:
    return {
        "value_id": value_id,
        "table_id": "DOCLING_001",
        "table_key": "docling_001",
        "label": "Total actif",
        "column": "2025",
        "raw_value": raw_value,
        "normalized_decimal": raw_value,
        "unit": "MAD",
        "period": "2025",
        "reporting_scope": "SOCIAL",
        "page": [1],
        "status": "NOT_CHECKED",
        "reasons": [],
        "provenance": {"bbox": [1, 2, 3, 4]},
    }


def _quality_files(tmp_path: Path, annotations: list[dict]) -> Path:
    annotations_path = tmp_path / "annotations.jsonl"
    annotations_path.write_text(
        "".join(json.dumps(item) + "\n" for item in annotations),
        encoding="utf-8",
    )
    quality_path = tmp_path / "quality_report.json"
    quality_path.write_text(
        json.dumps({"outputs": {"annotations": str(annotations_path)}}),
        encoding="utf-8",
    )
    return quality_path


def test_registry_covers_corpus_and_keeps_sector_indicators_distinct() -> None:
    registry = read_json(DEFAULT_REGISTRY)

    summary = validate_indicator_registry(registry)
    bank_indicators = registry["indicators"]

    assert summary["company_count"] == 15
    assert _indicator_match("Chiffre d'affaires", "BANKING", bank_indicators) is None
    assert (
        _indicator_match("Produit net bancaire", "BANKING", bank_indicators)[
            "indicator"
        ]
        == "BANKING_NET_REVENUE"
    )
    assert (
        _indicator_match("Résultat net par action", "GENERAL_CORPORATE", bank_indicators)
        is None
    )
    assert (
        _indicator_match(
            "Rendement des capitaux propres", "GENERAL_CORPORATE", bank_indicators
        )
        is None
    )
    assert (
        _indicator_match(
            "Résultat net part du Groupe", "GENERAL_CORPORATE", bank_indicators
        )["indicator"]
        == "NET_INCOME_GROUP_SHARE"
    )


def test_registry_rejects_industrial_revenue_for_banks() -> None:
    registry = copy.deepcopy(read_json(DEFAULT_REGISTRY))
    revenue = next(
        item for item in registry["indicators"] if item["indicator"] == "REVENUE"
    )
    revenue["forbidden_profiles"].remove("BANKING")

    try:
        validate_indicator_registry(registry)
    except ValueError as error:
        assert "bancaire" in str(error)
    else:
        raise AssertionError("Le profil bancaire aurait dû être rejeté")


def test_priority_queue_groups_duplicates_without_granting_verification(
    tmp_path: Path,
) -> None:
    registry = read_json(DEFAULT_REGISTRY)
    quality_path = _quality_files(
        tmp_path,
        [_annotation("value-1"), _annotation("value-2")],
    )
    report = {
        "concordance": [],
        "documents": [
            {
                "company_id": "s2m",
                "exercise_year": 2025,
                "source_pdf": "source.pdf",
                "quality_report": str(quality_path),
            }
        ],
    }

    queue = build_priority_queue(report, registry)

    assert len(queue) == 1
    assert queue[0]["occurrence_count"] == 2
    assert queue[0]["score_grants_verified"] is False
    assert queue[0]["current_status"] == "NOT_CHECKED"


def test_deterministic_equations_are_complementary_and_context_sensitive(
    tmp_path: Path,
) -> None:
    liabilities = _annotation("liabilities")
    liabilities["label"] = "Total passif"
    quality_path = _quality_files(
        tmp_path,
        [_annotation("assets"), liabilities],
    )
    reference_report = {
        "documents": [
            {
                "company_id": "s2m",
                "quality_report": str(quality_path),
            }
        ],
        "concordance": [
            {
                "reference_id": "assets",
                "currency": "MAD",
                "scale": 1,
                "accounting_perimeter": "SOCIAL",
                "raw_value": "100",
            },
            {
                "reference_id": "liabilities",
                "currency": "MAD",
                "scale": 1,
                "accounting_perimeter": "SOCIAL",
                "raw_value": "100",
            },
        ],
    }
    configuration = {
        "deterministic_checks": [
            {
                "check_id": "balance",
                "kind": "BALANCE_EQUALITY",
                "company_id": "s2m",
                "period": "2025",
                "scope": "SOCIAL",
                "left_reference_id": "assets",
                "right_reference_id": "liabilities",
            }
        ]
    }

    result = run_deterministic_checks(configuration, reference_report)[0]

    assert result["status"] == "PASS"
    assert result["verification_effect"] == "COMPLEMENTARY_ONLY"
    assert result["creates_verified_status"] is False

    reference_report["concordance"][1]["accounting_perimeter"] = "CONSOLIDATED"
    incompatible = run_deterministic_checks(configuration, reference_report)[0]
    assert incompatible["status"] == "NOT_CHECKED"
