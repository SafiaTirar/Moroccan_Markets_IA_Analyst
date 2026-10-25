import json
from decimal import Decimal
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.document_integrator import file_hash
from moroccan_markets_ai.data.parsers.financial_quality_gate import (
    QualityRules,
    _canonical_decimal,
    _find_annotations,
    evaluate_quality,
    load_admissible_values,
)


def make_integrated_document(
    tmp_path: Path,
    csv_body: str,
    *,
    coordinates: dict | None = None,
    table_id: str = "OCR_001",
) -> Path:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-synthetic")
    csv_path = tmp_path / "table.csv"
    csv_path.write_text(csv_body, encoding="utf-8")
    coordinate_path = None
    coordinate_sha256 = None
    if coordinates is not None:
        coordinate_file = tmp_path / "coordinates.json"
        coordinate_file.write_text(json.dumps(coordinates), encoding="utf-8")
        coordinate_path = str(coordinate_file)
        coordinate_sha256 = file_hash(coordinate_file)
    integrated = tmp_path / "unified_document.json"
    integrated.write_text(
        json.dumps(
            {
                "source_pdf": str(source),
                "source_sha256": file_hash(source),
                "tables": [
                    {
                        "table_id": table_id,
                        "source": "OCR",
                        "page": [2],
                        "reporting_scope": "SOCIAL",
                        "unit": "DH",
                        "csv_path": str(csv_path),
                        "coordinates_path": coordinate_path,
                        "coordinates_sha256": coordinate_sha256,
                        "sha256": file_hash(csv_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return integrated


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_quarantines_concatenated_missing_label_and_extra_column(
    tmp_path: Path,
) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,value_1,value_2_extra\n"
        "Crédit,4 687 453 518 598 596 599,\n"
        ",10 000,\n"
        "Résultat,20 000,30 000\n",
        coordinates={
            "rows": [
                {
                    "label": "Crédit",
                    "cells": [
                        {
                            "column": "value_1",
                            "value": "4 687 453 518 598 596 599",
                            "bbox": [1, 2, 3, 4],
                        }
                    ],
                }
            ]
        },
    )

    report = evaluate_quality(integrated, output_root=tmp_path / "quality")
    quarantine = read_jsonl(
        tmp_path
        / "quality"
        / f"source_{file_hash(tmp_path / 'source.pdf')[:12]}"
        / report["outputs"]["quarantine"]
    )

    codes = {
        reason["code"]
        for item in quarantine
        for reason in item["reasons"]
    }
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["summary"]["non_admissible_values"] == 3
    assert codes >= {
        "SUSPICIOUS_CONCATENATED_AMOUNT",
        "MISSING_ROW_LABEL",
        "UNEXPECTED_EXTRA_COLUMN",
    }
    concatenated = next(
        item
        for item in quarantine
        if any(
            reason["code"] == "SUSPICIOUS_CONCATENATED_AMOUNT"
            for reason in item["reasons"]
        )
    )
    assert concatenated["provenance"]["bbox"] == [1, 2, 3, 4]
    assert not concatenated["downstream_eligible"]


def test_verified_requires_named_reliable_reference(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        'label,S1 2026\nRésultat net,"12 345,67"\n',
    )
    untrusted = QualityRules(
        references=(
            {
                "table_id": "OCR_001",
                "label": "Résultat net",
                "column": "S1 2026",
                "value": "12345.67",
            },
        )
    )
    trusted = QualityRules(
        context_overrides=(
            {
                "table_id": "OCR_001",
                "column_periods": {"S1 2026": "S1 2026"},
                "unit": "DH",
                "scope": "SOCIAL",
            },
        ),
        references=(
            {
                "table_id": "OCR_001",
                "label": "Résultat net",
                "column": "S1 2026",
                "value": "12345.67",
                "reliable": True,
                "reference_id": "audited-statement-1",
                "source": "signed statement",
                "unit": "DH",
                "period": "S1 2026",
                "scope": "SOCIAL",
            },
        )
    )

    unchecked = evaluate_quality(
        integrated,
        rules=untrusted,
        output_root=tmp_path / "unchecked",
    )
    verified = evaluate_quality(
        integrated,
        rules=trusted,
        output_root=tmp_path / "verified",
    )

    assert unchecked["summary"]["by_status"]["VERIFIED"] == 0
    assert verified["summary"]["by_status"]["VERIFIED"] == 1
    assert verified["status"] == "VERIFIED"


def test_reliable_reference_mismatch_can_be_explicitly_rejected(
    tmp_path: Path,
) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,2026\nTotal actif,999\n",
    )
    rules = QualityRules(
        context_overrides=(
            {
                "table_id": "OCR_001",
                "column_periods": {"2026": "2026"},
                "unit": "MAD",
                "scope": "SOCIAL",
            },
        ),
        references=(
            {
                "table_id": "OCR_001",
                "label": "Total actif",
                "column": "2026",
                "value": "100",
                "reliable": True,
                "reference_id": "source-total-assets",
                "source": "original PDF",
                "unit": "MAD",
                "period": "2026",
                "scope": "SOCIAL",
                "mismatch_status": "REJECTED",
            },
        ),
        downstream_allowed_statuses=("VERIFIED",),
    )

    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "rejected",
    )

    assert report["summary"]["by_status"]["REJECTED"] == 1
    assert report["summary"]["admissible_values"] == 0


def test_arithmetic_pass_is_not_a_verification(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,2026\nActif,100\nPassif,100\n",
    )
    rules = QualityRules(
        comparisons=(
            {
                "name": "balance",
                "kind": "BALANCE",
                "left": {"label": "Actif", "column": "2026"},
                "right": {"label": "Passif", "column": "2026"},
                "unit": "DH",
                "period": "2026",
                "scope": "SOCIAL",
            },
        )
    )

    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "quality",
    )
    check = next(item for item in report["checks"] if item["check_id"] == "balance")

    assert check["outcome"] == "PASS"
    assert check["status"] == "NOT_CHECKED"
    assert report["summary"]["by_status"]["VERIFIED"] == 0


def test_failed_comparison_quarantines_both_values(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,2026\nActif,100\nPassif,90\n",
    )
    rules = QualityRules(
        comparisons=(
            {
                "name": "balance",
                "kind": "BALANCE",
                "left": {"label": "Actif", "column": "2026"},
                "right": {"label": "Passif", "column": "2026"},
                "unit": "DH",
                "period": "2026",
                "scope": "SOCIAL",
            },
        )
    )

    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "quality",
    )

    assert report["summary"]["non_admissible_values"] == 2
    assert report["summary"]["by_status"]["REVIEW_REQUIRED"] == 2


def test_cache_depends_on_rules_and_source_artifacts(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,value\nRésultat,10\n",
    )
    output = tmp_path / "quality"
    first = evaluate_quality(integrated, output_root=output)
    second = evaluate_quality(integrated, output_root=output)
    changed_rules = evaluate_quality(
        integrated,
        rules=QualityRules(max_integer_digits=10),
        output_root=output,
    )

    assert not first["cache_used"]
    assert second["cache_used"]
    assert not changed_rules["cache_used"]


def test_strict_downstream_policy_blocks_unverified_values(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,value\nRésultat,10\n",
    )

    report = evaluate_quality(
        integrated,
        rules=QualityRules(downstream_allowed_statuses=("VERIFIED",)),
        output_root=tmp_path / "quality",
    )

    assert report["status"] == "REVIEW_REQUIRED"
    assert report["summary"]["by_status"]["NOT_CHECKED"] == 1
    assert report["summary"]["admissible_values"] == 0
    assert report["summary"]["non_admissible_values"] == 1
    assert report["downstream_contract"]["allowed_statuses"] == ["VERIFIED"]
    assert load_admissible_values(
        Path(report["outputs"]["annotations"]).parent / "quality_report.json"
    ) == []


def test_context_override_preserves_detected_comparative_periods(
    tmp_path: Path,
) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,31-déc.-25,31-déc.-24\nRésultat net,100,90\n",
    )
    rules = QualityRules(
        context_overrides=(
            {
                "table_id": "OCR_001",
                "column_periods": {"31-déc.-25": "2025"},
                "unit": "MAD",
                "scope": "SOCIAL",
            },
        ),
        downstream_allowed_statuses=("VERIFIED",),
    )

    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "quality",
    )
    annotations = read_jsonl(Path(report["outputs"]["annotations"]))

    assert {item["column"]: item["period"] for item in annotations} == {
        "31-déc.-25": "2025",
        "31-déc.-24": "2024",
    }


def test_cell_context_override_does_not_leak_to_other_values(
    tmp_path: Path,
) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,value_1\nRésultat net,100\nCapitaux propres,200\n",
    )
    rules = QualityRules(
        context_overrides=(
            {
                "table_id": "OCR_001",
                "label": "Résultat net",
                "column": "value_1",
                "column_periods": {"value_1": "2025"},
                "unit": "KMAD",
                "scope": "CONSOLIDATED",
            },
        ),
        downstream_allowed_statuses=("VERIFIED",),
    )

    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "quality",
    )
    annotations = {
        item["label"]: item
        for item in read_jsonl(Path(report["outputs"]["annotations"]))
    }

    assert annotations["Résultat net"]["unit"] == "KMAD"
    assert annotations["Résultat net"]["period"] == "2025"
    assert annotations["Résultat net"]["reporting_scope"] == "CONSOLIDATED"
    assert annotations["Capitaux propres"]["unit"] == "DH"
    assert annotations["Capitaux propres"]["period"] is None
    assert annotations["Capitaux propres"]["reporting_scope"] == "SOCIAL"


def test_percentages_are_values_and_alphanumeric_headers_are_ignored(
    tmp_path: Path,
) -> None:
    integrated = make_integrated_document(
        tmp_path,
        'label,value\nMarge,"21,7%"\nEntête,S1 2026\n',
    )

    report = evaluate_quality(
        integrated,
        output_root=tmp_path / "quality",
    )
    annotations = read_jsonl(
        tmp_path
        / "quality"
        / f"source_{file_hash(tmp_path / 'source.pdf')[:12]}"
        / report["outputs"]["annotations"]
    )

    assert report["summary"]["total_values"] == 1
    assert annotations[0]["normalized_decimal"] == "21.7"
    assert annotations[0]["unit"] == "%"


def test_dotted_thousands_are_parsed_without_guessing_decimal_scale() -> None:
    assert _canonical_decimal("279.980.937") == Decimal(279980937)
    assert _canonical_decimal("21.551.405") == Decimal(21551405)
    assert _canonical_decimal("21.551") == Decimal("21.551")


def test_subtotal_and_sign_controls_are_configurable(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,2026\nProduit A,60\nProduit B,40\nTotal,100\nPerte,-5\n",
    )
    rules = QualityRules(
        subtotals=(
            {
                "name": "products_total",
                "total": {"label": "Total", "column": "2026"},
                "components": [
                    {"label": "Produit A", "column": "2026"},
                    {"label": "Produit B", "column": "2026"},
                ],
                "unit": "DH",
                "period": "2026",
                "scope": "SOCIAL",
            },
        ),
        sign_rules=(
            {
                "name": "loss_is_negative",
                "selector": {"label": "Perte", "column": "2026"},
                "expected": "NEGATIVE",
            },
        ),
    )

    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "quality",
    )
    checks = {item["check_id"]: item for item in report["checks"]}

    assert checks["products_total"]["outcome"] == "PASS"
    assert checks["products_total"]["status"] == "NOT_CHECKED"
    assert checks["loss_is_negative"]["outcome"] == "PASS"
    assert checks["loss_is_negative"]["status"] == "NOT_CHECKED"


def test_modified_coordinates_are_rejected(tmp_path: Path) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,value\nRésultat,10\n",
        coordinates={"rows": []},
    )
    evaluate_quality(integrated, output_root=tmp_path / "quality")
    (tmp_path / "coordinates.json").write_text(
        json.dumps({"rows": [{"cells": []}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Coordonnées OCR modifiées"):
        evaluate_quality(integrated, output_root=tmp_path / "quality")


def test_empty_header_is_a_real_column_selector() -> None:
    annotations = [
        {
            "table_id": "DOCLING_001",
            "label": "Résultat net",
            "column": "",
            "page": [1],
            "status": "NOT_CHECKED",
            "reasons": [],
        },
        {
            "table_id": "DOCLING_001",
            "label": "Résultat net",
            "column": "2024",
            "page": [1],
            "status": "NOT_CHECKED",
            "reasons": [],
        },
    ]

    selected = _find_annotations(
        annotations,
        {
            "table_id": "DOCLING_001",
            "label": "Résultat net",
            "column": "",
            "page": [1],
        },
    )

    assert len(selected) == 1
    assert selected[0]["column"] == ""


def test_review_decision_rejects_value_and_downstream_contract_excludes_it(
    tmp_path: Path,
) -> None:
    integrated = make_integrated_document(
        tmp_path,
        "label,value\nCrédit,4 687 453 518 598 596 599\nRésultat,10\n",
    )
    rules = QualityRules(
        review_decisions=(
            {
                "name": "visual_review",
                "selector": {
                    "reason_code": "SUSPICIOUS_CONCATENATED_AMOUNT"
                },
                "decision": "REJECTED",
                "reliable": True,
                "reference_id": "source-page-1",
                "source": "signed source page 1",
            },
        )
    )
    report = evaluate_quality(
        integrated,
        rules=rules,
        output_root=tmp_path / "quality",
    )
    report_path = Path(report["outputs"]["annotations"]).parent / "quality_report.json"
    downstream = load_admissible_values(report_path)
    quarantined = read_jsonl(Path(report["outputs"]["quarantine"]))

    assert len(downstream) == 1
    assert downstream[0]["raw_value"] == "10"
    assert quarantined[0]["status"] == "REJECTED"

