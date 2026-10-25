import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.processing.data_04_14_report import _prior_documents


def write_references(path: Path, documents: list[tuple[str, int]]) -> None:
    path.write_text(
        json.dumps(
            {
                "documents": [
                    {"company_id": company_id, "exercise_year": year}
                    for company_id, year in documents
                ]
            }
        ),
        encoding="utf-8",
    )


def test_prior_documents_combines_disjoint_reference_files(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_references(first, [("a", 2023), ("a", 2024)])
    write_references(second, [("b", 2025)])

    assert _prior_documents((first, second)) == {
        ("a", 2023),
        ("a", 2024),
        ("b", 2025),
    }


def test_prior_documents_rejects_cross_mission_duplicate(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_references(first, [("a", 2024)])
    write_references(second, [("a", 2024)])

    with pytest.raises(ValueError, match="plusieurs référentiels"):
        _prior_documents((first, second))
