import json
from decimal import Decimal
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    DEFAULT_HANDOFF_DIR,
    candidate_id,
    load_candidates,
)

GOOD = {
    "nom": "TOTAL ACTIF",
    "annee": 2025,
    "valeur": 795461302.0,
    "ligne_source": "TOTAL ACTIF  |  | 795 461 302  | 726 492 948",
    "entreprise": "attijariwafa_bank",
    "unite": None,
    "perimetre": None,
    "source_table": "DOCLING_083",
    "source_page": 95,
    "source_ligne": 25,
    "validation_status": "NOT_CHECKED",
    "indicator": "TOTAL_ASSETS",
    "report_year": 2025,
}


def _write(tmp_path: Path, records) -> Path:
    path = tmp_path / "candidats.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return path


def _variant(**changes):
    record = dict(GOOD)
    for key, value in changes.items():
        if value is ...:
            record.pop(key)
        else:
            record[key] = value
    return record


def _codes(result):
    return {r["code"] for rec in result.rejected for r in rec.reasons}


def test_valid_candidate_is_loaded(tmp_path):
    result = load_candidates(_write(tmp_path, [GOOD]))
    assert result.input_count == 1 and len(result.loaded) == 1 and not result.rejected
    loaded = result.loaded[0]
    assert loaded.candidate.valeur == Decimal("795461302.0")
    assert loaded.profile == "BANKING"
    assert loaded.original == GOOD  # la valeur d'origine est conservée


@pytest.mark.parametrize(
    ("record", "code"),
    [
        (_variant(valeur=...), "MISSING_FIELD"),
        (_variant(source_table=...), "MISSING_FIELD"),
        (_variant(valeur="12 abc"), "NON_NUMERIC_VALUE"),
        (_variant(valeur=None), "NON_NUMERIC_VALUE"),
        (_variant(annee="2025"), "INVALID_FIELD"),
        (_variant(annee=2025.5), "INVALID_FIELD"),
        (_variant(source_page=0), "INVALID_FIELD"),
        (_variant(source_ligne=-1), "INVALID_FIELD"),
        (_variant(annee=2026), "INVALID_OBSERVATION_YEAR"),
        (_variant(annee=1990), "INVALID_OBSERVATION_YEAR"),
        (_variant(report_year=2019), "INVALID_REPORT_YEAR"),
        (_variant(indicator="FOO"), "UNKNOWN_INDICATOR"),
        (_variant(entreprise="inconnue"), "UNKNOWN_COMPANY"),
        (_variant(validation_status="VERIFIED"), "STATUS_NOT_ALLOWED"),
    ],
)
def test_invalid_candidates_are_rejected_with_reason(tmp_path, record, code):
    result = load_candidates(_write(tmp_path, [record]))
    assert not result.loaded and len(result.rejected) == 1
    assert code in _codes(result)


def test_non_object_entry_is_rejected_not_lost(tmp_path):
    result = load_candidates(_write(tmp_path, [GOOD, "oops", 42]))
    assert result.input_count == 3
    assert len(result.loaded) == 1 and len(result.rejected) == 2


def test_root_must_be_a_list(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError):
        load_candidates(path)


def test_unknown_fields_are_kept_and_reported(tmp_path):
    result = load_candidates(_write(tmp_path, [_variant(extra_info="x")]))
    assert result.loaded[0].unknown_fields == ("extra_info",)
    assert result.loaded[0].original["extra_info"] == "x"


def test_candidate_id_is_stable_and_discriminant():
    assert candidate_id(GOOD) == candidate_id(dict(GOOD))
    assert candidate_id(GOOD) != candidate_id(_variant(annee=2024))


def test_utf8_labels_survive(tmp_path):
    result = load_candidates(
        _write(tmp_path, [_variant(nom="Résultat net de l’exercice")])
    )
    assert result.loaded[0].candidate.nom == "Résultat net de l’exercice"


@pytest.mark.skipif(
    not (DEFAULT_HANDOFF_DIR / "candidats_fqg.json").exists(),
    reason="handoff_fqg_45 absent (récupérer depuis feature/qwen3-pydantic-extraction)",
)
def test_real_handoff_has_no_silent_loss():
    result = load_candidates()
    assert result.input_count == 329
    assert len(result.loaded) + len(result.rejected) == 329