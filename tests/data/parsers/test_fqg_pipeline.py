import json
from pathlib import Path

import pytest

from moroccan_markets_ai.data.parsers import financial_quality_gate
from moroccan_markets_ai.data.parsers.fqg_handoff_loader import DEFAULT_HANDOFF_DIR
from moroccan_markets_ai.data.parsers.fqg_pipeline import (
    report_markdown,
    reports_without_candidate,
    run,
)

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
ANOMALIES = [
    {"entreprise": "stroc", "report_year": 2024, "raison": "AUCUN_CANDIDAT_EXTRAIT"},
    {
        "entreprise": "attijariwafa_bank",
        "report_year": 2025,
        "table_id": "DOCLING_099",
        "pages": [1],
        "raison": "TABLEAU_TO_REVIEW",
    },
]
BILAN = [
    {"entreprise": "attijariwafa_bank", "annee": 2025, "candidats": 3},
    {"entreprise": "dari_couspate", "annee": 2023, "candidats": 0},
]


def write_inputs(tmp_path: Path, candidates) -> tuple[Path, Path]:
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    (handoff / "candidats_fqg.json").write_text(
        json.dumps(candidates, ensure_ascii=False), encoding="utf-8"
    )
    (handoff / "anomalies.json").write_text(json.dumps(ANOMALIES), encoding="utf-8")
    (handoff / "bilan.json").write_text(json.dumps(BILAN), encoding="utf-8")
    return handoff / "candidats_fqg.json", handoff / "anomalies.json"


def candidates():
    passif = dict(
        BASE,
        indicator="TOTAL_LIABILITIES_AND_EQUITY",
        nom="TOTAL PASSIF",
        source_table="DOCLING_084",
        ligne_source="TOTAL PASSIF | 795 461 302",
    )
    broken = dict(BASE, valeur="abc")
    return [BASE, dict(BASE), passif, broken]


def test_pipeline_writes_all_outputs_without_loss(tmp_path):
    cand_path, anom_path = write_inputs(tmp_path, candidates())
    out = tmp_path / "out"
    report = run(cand_path, anom_path, out, pdf_sources=tmp_path / "none.json")

    for name in (
        "decisions.jsonl",
        "admissible_values.jsonl",
        "verified_sample.json",
        "rejected_sample.json",
        "review_required.json",
        "fqg_report.json",
        "fqg_report.md",
    ):
        assert (out / name).exists(), name

    decisions = (out / "decisions.jsonl").read_text("utf-8").splitlines()
    assert len(decisions) == report["candidates_received"] == 4
    assert report["structurally_valid"] == 3
    assert report["structurally_rejected"] == 1
    assert report["exact_duplicates"] == 1
    assert report["rejected"] == 2  # 1 structurel + 1 doublon exact


def test_nothing_is_admissible_without_human_proof(tmp_path):
    cand_path, anom_path = write_inputs(tmp_path, candidates())
    out = tmp_path / "out"
    report = run(cand_path, anom_path, out, pdf_sources=tmp_path / "none.json")
    assert report["verified"] == 0
    assert (out / "admissible_values.jsonl").read_text("utf-8") == ""
    assert report["downstream_allowed_statuses"] == ["VERIFIED"]


def test_report_contains_every_required_measure(tmp_path):
    cand_path, anom_path = write_inputs(tmp_path, candidates())
    report = run(cand_path, anom_path, tmp_path / "out", pdf_sources=tmp_path / "x")
    for key in (
        "candidates_received",
        "structurally_valid",
        "exact_duplicates",
        "value_conflict_groups",
        "verified",
        "rejected",
        "remaining_to_check",
        "anomalies_by_category",
        "reports_without_candidate",
    ):
        assert key in report, key
    assert report["anomalies_by_category"] == {
        "AUCUN_CANDIDAT_EXTRAIT": 1,
        "TABLEAU_TO_REVIEW": 1,
    }
    markdown = report_markdown(report)
    assert "Candidats reçus" in markdown and "Rapports sans candidat" in markdown


def test_reports_without_candidate_merges_bilan_and_anomalies():
    result = reports_without_candidate(BILAN, ANOMALIES)
    assert result == [
        {"company": "dari_couspate", "report_year": 2023},
        {"company": "stroc", "report_year": 2024},
    ]


def test_cli_handoff_mode_keeps_legacy_mode(tmp_path, capsys):
    cand_path, anom_path = write_inputs(tmp_path, candidates())
    out = tmp_path / "out"
    financial_quality_gate.main(
        [
            "--candidates",
            str(cand_path),
            "--anomalies",
            str(anom_path),
            "--output",
            str(out),
            "--pdf-sources",
            str(tmp_path / "none.json"),
        ]
    )
    assert '"candidates_received": 4' in capsys.readouterr().out
    assert (out / "fqg_report.md").exists()
    with pytest.raises(SystemExit):  # l'ancien mode exige toujours son argument
        financial_quality_gate.main([])


@pytest.mark.skipif(
    not (DEFAULT_HANDOFF_DIR / "candidats_fqg.json").exists(),
    reason="handoff_fqg_45 absent",
)
def test_real_handoff_report(tmp_path):
    report = run(
        DEFAULT_HANDOFF_DIR / "candidats_fqg.json",
        DEFAULT_HANDOFF_DIR / "anomalies.json",
        tmp_path,
        human_decisions=tmp_path / "absent.json",
    )
    assert report["candidates_received"] == 329
    assert report["structurally_valid"] == 329
    assert report["verified"] == 0
    assert report["anomalies_total"] == 133
    assert len(report["reports_without_candidate"]) == 13
    assert sum(report["status_counts"].values()) == 329


REAL_HUMAN_DECISIONS = (
    DEFAULT_HANDOFF_DIR.parent / "fqg_validation" / "human_decisions.json"
)


@pytest.mark.skipif(
    not (DEFAULT_HANDOFF_DIR / "candidats_fqg.json").exists()
    or not REAL_HUMAN_DECISIONS.exists(),
    reason="handoff_fqg_45 ou décisions humaines du jour 4 absents",
)
def test_sample_is_the_one_checked_on_day_4(tmp_path):
    """Régression : l'échantillon doit être choisi avant les décisions humaines."""
    human = json.loads(REAL_HUMAN_DECISIONS.read_text("utf-8"))
    human = human if isinstance(human, list) else human["decisions"]
    report = run(
        DEFAULT_HANDOFF_DIR / "candidats_fqg.json",
        DEFAULT_HANDOFF_DIR / "anomalies.json",
        tmp_path,
        human_decisions=REAL_HUMAN_DECISIONS,
    )
    decided = [d["decision"] for d in human if d.get("decision")]
    counts = report["sample"]["status_counts"]
    assert report["sample"]["refused_human_decisions"] == 0
    for status in ("VERIFIED", "PDF_CONFIRMED", "REJECTED"):
        assert counts[status] == decided.count(status), status
