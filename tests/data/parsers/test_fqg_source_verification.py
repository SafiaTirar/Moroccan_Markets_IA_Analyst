import hashlib
import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers.fqg_candidate_checks import run_checks
from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    DEFAULT_HANDOFF_DIR,
    load_anomalies,
    load_candidates,
)
from moroccan_markets_ai.data.parsers.fqg_source_verification import (
    apply_human_decisions,
    auto_check,
    build_pdf_sources,
    build_worksheet,
    detect_scope,
    detect_unit,
    human_decision_template,
    select_sample,
    write_sample_outputs,
)

SHA = "766da5ac174bfd22e1a9aac59fd5465bb51b2249ed8143268afba5235f1e3afb"
SOURCES = {
    ("attijariwafa_bank", 2025): {
        "company": "attijariwafa_bank",
        "report_year": 2025,
        "pdf_file": "AWB_RFA_2025.pdf",
        "pdf_relative_path": "data/raw/ammc/pdfs/annual/attijariwafa_bank/2025/AWB_RFA_2025.pdf",
        "pdf_sha256": SHA,
    }
}
BASE = {
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


def decisions_for(tmp_path: Path, records=None):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(records or [BASE]), encoding="utf-8")
    decisions, _ = run_checks(load_candidates(path).loaded, [])
    return decisions


def full_evidence(**changes):
    evidence = {
        "pdf_file": "AWB_RFA_2025.pdf",
        "pdf_sha256": SHA,
        "pdf_page": 95,
        "printed_page": 93,
        "table_title": "Bilan IFRS consolidé",
        "value_read": "795 461 302",
        "sign": "+",
        "unit": "KMAD",
        "scope": "CONSOLIDE",
        "column_year": 2025,
        "currency": "MAD",
    }
    evidence.update(changes)
    return evidence


def human(decisions, decision="VERIFIED", **evidence_changes):
    return {
        "candidate_id": decisions[0].loaded.candidate_id,
        "decision": decision,
        "reason": "Montant, colonne 2025, unité KMAD et périmètre consolidé lus page 95.",
        "checked_by": "Rihab",
        "checked_at": "2026-11-09",
        "evidence": full_evidence(**evidence_changes),
    }


def test_complete_evidence_gives_verified(tmp_path):
    decisions = decisions_for(tmp_path)
    refused = apply_human_decisions(decisions, [human(decisions)], SOURCES)
    assert refused == []
    assert decisions[0].status == "VERIFIED"
    assert decisions[0].evidence["unit"] == "KMAD"
    assert decisions[0].to_dict()["original"]["valeur"] == 795461302.0


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"unit": ""}, "unit"),
        ({"scope": ""}, "scope"),
        ({"pdf_sha256": ""}, "pdf_sha256"),
        ({"column_year": 2024}, "colonne"),
        ({"value_read": "795 461 300"}, "valeur lue"),
        ({"sign": "-"}, "valeur lue"),
        ({"pdf_sha256": "a" * 64}, "SHA-256"),
    ],
)
def test_incomplete_or_wrong_evidence_is_refused(tmp_path, changes, expected):
    decisions = decisions_for(tmp_path)
    refused = apply_human_decisions(decisions, [human(decisions, **changes)], SOURCES)
    assert decisions[0].status == "NOT_CHECKED"  # inchangé
    assert any(expected in p for p in refused[0]["problems"])


def test_pdf_confirmed_needs_less_evidence(tmp_path):
    decisions = decisions_for(tmp_path)
    row = human(
        decisions, "PDF_CONFIRMED", unit="", scope="", pdf_sha256="", column_year=None
    )
    assert apply_human_decisions(decisions, [row], SOURCES) == []
    assert decisions[0].status == "PDF_CONFIRMED"


def test_rejected_with_reason(tmp_path):
    decisions = decisions_for(tmp_path)
    row = human(decisions, "REJECTED", value_read="728 502 650")
    row["reason"] = "Le PDF indique 728 502 650 : mauvaise ligne extraite."
    assert apply_human_decisions(decisions, [row], SOURCES) == []
    assert decisions[0].status == "REJECTED"


def test_missing_reviewer_is_refused(tmp_path):
    decisions = decisions_for(tmp_path)
    row = human(decisions)
    row["checked_by"] = ""
    refused = apply_human_decisions(decisions, [row], SOURCES)
    assert refused and decisions[0].status == "NOT_CHECKED"


def test_empty_decision_changes_nothing(tmp_path):
    decisions = decisions_for(tmp_path)
    sheet = build_worksheet(decisions, SOURCES, tmp_path)
    template = human_decision_template(sheet)
    assert apply_human_decisions(decisions, template, SOURCES) == []
    assert decisions[0].status == "NOT_CHECKED"
    assert sheet[0]["pdf_sha256_expected"] == SHA
    assert sheet[0]["auto_check"]["proposal"] == "SOURCES_NOT_AVAILABLE"


def _handover(tmp_path: Path, total_passif: str = "795 461 302") -> Path:
    report = tmp_path / "attijariwafa_bank" / "2025"
    (report / "tables").mkdir(parents=True)
    rows = [
        "Bilan consolidé au 31 décembre 2025 ,,,( en  milliers de dirhams)",
        "ACTIF ,Notes ,31/12/2025 ,31/12/2024",
    ]
    rows += [f"ligne {i},,1,2" for i in range(24)]
    rows.append(f"TOTAL ACTIF ,,{total_passif} ,726 492 948")
    csv_path = report / "tables" / "table_083.csv"
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    csv_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    unified = {"tables": [{"table_id": "DOCLING_083", "page": [95], "sha256": csv_sha}]}
    (report / "unified_document.json").write_text(json.dumps(unified), encoding="utf-8")
    return tmp_path


def test_auto_check_reads_csv_and_unified_document(tmp_path):
    decisions = decisions_for(tmp_path)
    root = _handover(tmp_path / "handover")
    check = auto_check(decisions[0], SOURCES[("attijariwafa_bank", 2025)], root)
    assert check["csv_row_found"] is True
    assert check["column_year"] == 2025
    assert check["unit_hint"] == "KMAD"
    assert check["scope_hint"] == "CONSOLIDE"
    assert check["unified_page_ok"] is True
    assert check["unified_csv_sha256_ok"] is True
    # le PDF manque : aucune proposition de validation n'est possible
    assert check["proposal"] == "PROPOSE_REVIEW_REQUIRED"


def test_template_prefills_hints_but_never_the_decision(tmp_path):
    decisions = decisions_for(tmp_path)
    root = _handover(tmp_path / "handover")
    sheet = build_worksheet(decisions, SOURCES, root)
    row = human_decision_template(sheet)[0]
    assert row["decision"] is None and row["checked_by"] == ""
    assert row["evidence"]["unit"] == "KMAD"
    assert row["evidence"]["column_year"] == 2025


@pytest.mark.parametrize(
    ("text", "units"),
    [
        ("BILAN (en milliers de dirhams)", ["KMAD"]),
        ("ACTIF (En millions de dirhams)", ["MMAD"]),
        ("PASSIF (En dirhams)", ["MAD"]),
        ("Résultat par action en MAD", []),
        ("EN MILLIIERS DE MAD", ["KMAD"]),
    ],
)
def test_unit_detection(text, units):
    assert detect_unit(text) == units


def test_scope_detection():
    assert detect_scope("BILAN CONSOLIDÉ IFRS") == ["CONSOLIDE"]
    assert detect_scope("Comptes sociaux - Bilan") == ["SOCIAL"]


def test_outputs_separate_verified_rejected_review(tmp_path):
    second = dict(BASE, annee=2024, valeur=726492948.0)
    decisions = decisions_for(tmp_path, [BASE, second])
    apply_human_decisions(decisions, [human(decisions)], SOURCES)
    counts = write_sample_outputs(tmp_path / "out", decisions, [])
    assert counts["verified_or_pdf_confirmed"] == 1
    assert counts["still_to_review"] == 1
    verified = json.loads(
        (tmp_path / "out" / "verified_sample.json").read_text("utf-8")
    )
    assert verified[0]["evidence"]["checked_by"] == "Rihab"
    assert verified[0]["normalized"]["source_page"] == 95


@pytest.mark.skipif(
    not (DEFAULT_HANDOFF_DIR / "candidats_fqg.json").exists(),
    reason="handoff_fqg_45 absent",
)
def test_real_sample_is_representative():
    decisions, _ = run_checks(load_candidates().loaded, load_anomalies())
    sample = select_sample(decisions)
    assert len(sample) >= 30
    assert len({d.c.entreprise for d in sample}) >= 10
    assert {d.c.report_year for d in sample} == {2023, 2024, 2025}
    assert len({d.c.indicator for d in sample}) >= 6
    assert {d.status for d in sample} >= {"NOT_CHECKED", "REVIEW_REQUIRED"}
    assert len({d.loaded.candidate_id for d in sample}) == len(sample)


def test_pdf_sources_built_from_ocr_metadata(tmp_path):
    folder = tmp_path / f"AWB_RFA_2025_{SHA[:12]}" / "cfg"
    folder.mkdir(parents=True)
    meta = {
        "source_pdf": "/Users/x/data/raw/ammc/pdfs/annual/attijariwafa_bank/2025/AWB_RFA_2025.pdf",
        "source_sha256": SHA,
    }
    (folder / "ocr_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    other = tmp_path / "S2M_RFS_S1_26_x" / "cfg"
    other.mkdir(parents=True)
    (other / "ocr_metadata.json").write_text(
        json.dumps({"source_pdf": "/x/S2M_RFS_S1_26.pdf", "source_sha256": SHA}),
        encoding="utf-8",
    )
    registry = build_pdf_sources(tmp_path)
    assert registry["reports"] == [SOURCES[("attijariwafa_bank", 2025)]]
