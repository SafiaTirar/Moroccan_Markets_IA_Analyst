"""Pipeline complet du Financial Quality Gate sur les candidats AMMC (jour 5).

Une seule commande enchaîne :
    chargement (jour 2) -> contrôles (jour 3) -> décisions humaines (jour 4)
    -> fichiers de sortie + rapport récapitulatif (jour 5).

Usage :
    python -m moroccan_markets_ai.data.parsers.financial_quality_gate \\
      --candidates data/quality/ammc/handoff_fqg_45/candidats_fqg.json \\
      --anomalies data/quality/ammc/handoff_fqg_45/anomalies.json \\
      --output data/quality/ammc/fqg_validation

Seuls les candidats VERIFIED (décision humaine avec preuve complète) sont
écrits dans ``admissible_values.jsonl``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.parsers.fqg_candidate_checks import (
    CandidateDecision,
    CheckSummary,
    run_checks,
)
from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    DEFAULT_CORPUS,
    DEFAULT_HANDOFF_DIR,
    DEFAULT_REGISTRY,
    HANDOFF_SCHEMA_VERSION,
    LoadResult,
    load_anomalies,
    load_bilan,
    load_candidates,
    read_json,
)
from moroccan_markets_ai.data.parsers.fqg_source_verification import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PDF_SOURCES,
    apply_human_decisions,
    load_pdf_sources,
    select_sample,
    write_json,
    write_sample_outputs,
)

DOWNSTREAM_ALLOWED_STATUSES = ("VERIFIED",)  # règle stricte, jamais NOT_CHECKED
STATUS_ORDER = (
    "VERIFIED",
    "PDF_CONFIRMED",
    "NOT_CHECKED",
    "REVIEW_REQUIRED",
    "REJECTED",
)


def _relative(path: Path) -> str:
    """Chemin relatif au projet quand c'est possible (pas de chemin machine)."""
    from moroccan_markets_ai.data.parsers.fqg_handoff_loader import PROJECT_ROOT

    path = Path(path)
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def reports_without_candidate(
    bilan: list[dict[str, Any]], anomalies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rapports (entreprise, exercice) n'ayant produit aucun candidat."""
    found = {
        (row["entreprise"], row["annee"])
        for row in bilan
        if int(row.get("candidats", 0)) == 0
    }
    found |= {
        (a["entreprise"], a["report_year"])
        for a in anomalies
        if a.get("raison") == "AUCUN_CANDIDAT_EXTRAIT"
    }
    return [{"company": c, "report_year": y} for c, y in sorted(found)]


def build_report(
    load_result: LoadResult,
    decisions: list[CandidateDecision],
    summary: CheckSummary,
    anomalies: list[dict[str, Any]],
    bilan: list[dict[str, Any]],
    sample: list[CandidateDecision],
    refused: list[dict[str, Any]],
    inputs: dict[str, str],
) -> dict[str, Any]:
    statuses = Counter(d.status for d in decisions)
    statuses["REJECTED"] += len(load_result.rejected)  # rejets structurels
    reason_codes = Counter(
        r["code"]
        for d in decisions
        for r in d.reasons
        if r["severity"] not in ("INFO", "HUMAN")
    )
    sample_statuses = Counter(d.status for d in sample)
    remaining = (
        statuses["NOT_CHECKED"]
        + statuses["REVIEW_REQUIRED"]
        + statuses["PDF_CONFIRMED"]
    )
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "inputs": inputs,
        "downstream_allowed_statuses": list(DOWNSTREAM_ALLOWED_STATUSES),
        "candidates_received": load_result.input_count,
        "structurally_valid": len(load_result.loaded),
        "structurally_rejected": len(load_result.rejected),
        "exact_duplicates": summary.exact_duplicates,
        "value_conflict_groups": summary.conflict_groups,
        "candidates_in_conflict": sum(
            1
            for d in decisions
            if d.conflict_group and d.conflict_group.startswith("conflict-")
        ),
        "assets_liabilities_mismatches": summary.balance_mismatches,
        "net_income_equal_group_share": summary.net_income_equal_group_share,
        "verified": statuses["VERIFIED"],
        "pdf_confirmed": statuses["PDF_CONFIRMED"],
        "rejected": statuses["REJECTED"],
        "review_required": statuses["REVIEW_REQUIRED"],
        "not_checked": statuses["NOT_CHECKED"],
        "remaining_to_check": remaining,
        "status_counts": {s: statuses[s] for s in STATUS_ORDER},
        "anomalies_by_category": dict(
            sorted(Counter(a.get("raison", "?") for a in anomalies).items())
        ),
        "anomalies_total": len(anomalies),
        "reports_without_candidate": reports_without_candidate(bilan, anomalies),
        "review_reasons": dict(reason_codes.most_common()),
        "sample": {
            "size": len(sample),
            "companies": len({d.c.entreprise for d in sample}),
            "report_years": sorted({d.c.report_year for d in sample}),
            "indicators": sorted({d.c.indicator for d in sample}),
            "status_counts": {s: sample_statuses[s] for s in STATUS_ORDER},
            "refused_human_decisions": len(refused),
        },
        "status_by_company": {
            company: dict(
                Counter(d.status for d in decisions if d.c.entreprise == company)
            )
            for company in sorted({d.c.entreprise for d in decisions})
        },
    }


def report_markdown(report: dict[str, Any]) -> str:
    def table(rows: list[tuple[str, Any]]) -> str:
        lines = ["| Mesure | Valeur |", "|---|---:|"]
        lines += [f"| {label} | {value} |" for label, value in rows]
        return "\n".join(lines)

    sample = report["sample"]
    lines = [
        "# Rapport du Financial Quality Gate — candidats AMMC",
        "",
        f"Généré le {report['generated_at']} — schéma `{report['schema_version']}`.",
        "",
        "## Synthèse",
        "",
        table(
            [
                ("Candidats reçus", report["candidates_received"]),
                ("Structurellement valides", report["structurally_valid"]),
                (
                    "Rejetés à la validation structurelle",
                    report["structurally_rejected"],
                ),
                ("Doublons exacts", report["exact_duplicates"]),
                ("Groupes de valeurs en conflit", report["value_conflict_groups"]),
                ("Candidats en conflit", report["candidates_in_conflict"]),
                ("Total actif ≠ total passif", report["assets_liabilities_mismatches"]),
                ("**Vérifiés (VERIFIED)**", f"**{report['verified']}**"),
                ("Confirmés PDF (PDF_CONFIRMED)", report["pdf_confirmed"]),
                ("Rejetés (REJECTED)", report["rejected"]),
                ("À revoir (REVIEW_REQUIRED)", report["review_required"]),
                ("Non contrôlés (NOT_CHECKED)", report["not_checked"]),
                ("**Restant à contrôler**", f"**{report['remaining_to_check']}**"),
            ]
        ),
        "",
        (
            "Seuls les candidats `VERIFIED` sont admissibles en aval "
            "(`admissible_values.jsonl`)."
        ),
        "",
        "## Anomalies de l'Extraction par catégorie",
        "",
        table(list(report["anomalies_by_category"].items())),
        "",
        "## Rapports sans candidat",
        "",
    ]
    missing = report["reports_without_candidate"]
    lines += [f"- {r['company']} {r['report_year']}" for r in missing] or ["- aucun"]
    lines += [
        "",
        "## Motifs de revue (hors INFO)",
        "",
        table(list(report["review_reasons"].items())),
        "",
        "## Échantillon vérifié contre les sources (jour 4)",
        "",
        (
            f"{sample['size']} candidats, {sample['companies']} entreprises, exercices "
            f"{', '.join(map(str, sample['report_years']))}, "
            f"{len(sample['indicators'])} indicateurs."
        ),
        "",
        table(list(sample["status_counts"].items())),
        "",
        (
            f"Décisions humaines refusées (preuve incomplète) : "
            f"{sample['refused_human_decisions']}."
        ),
        "",
        "## Statuts par entreprise",
        "",
        "| Entreprise | " + " | ".join(STATUS_ORDER) + " |",
        "|---|" + "---:|" * len(STATUS_ORDER),
    ]
    for company, counts in report["status_by_company"].items():
        cells = " | ".join(str(counts.get(s, 0)) for s in STATUS_ORDER)
        lines.append(f"| {company} | {cells} |")
    return "\n".join(lines) + "\n"


def run(
    candidates: Path,
    anomalies_path: Path,
    output: Path,
    bilan_path: Path | None = None,
    human_decisions: Path | None = None,
    pdf_sources: Path = DEFAULT_PDF_SOURCES,
    registry: Path = DEFAULT_REGISTRY,
    corpus: Path = DEFAULT_CORPUS,
) -> dict[str, Any]:
    load_result = load_candidates(candidates, registry, corpus)
    anomalies = load_anomalies(anomalies_path)
    bilan_path = bilan_path or Path(candidates).with_name("bilan.json")
    bilan = load_bilan(bilan_path) if bilan_path.exists() else []
    decisions, summary = run_checks(load_result.loaded, anomalies)

    # L'échantillon est choisi sur les statuts automatiques, AVANT d'appliquer
    # les décisions humaines : sinon la sélection (qui dépend du statut)
    # désignerait d'autres candidats que ceux vérifiés au jour 4.
    sample = select_sample(decisions)

    human_decisions = human_decisions or output / "human_decisions.json"
    sources = load_pdf_sources(pdf_sources)
    refused: list[dict[str, Any]] = []
    if human_decisions.exists():
        refused = apply_human_decisions(decisions, read_json(human_decisions), sources)

    write_sample_outputs(output, sample, refused)

    all_records = [d.to_dict() for d in decisions]
    all_records += [
        {
            "candidate_id": r.candidate_id,
            "input_index": r.input_index,
            "original": r.original,
            "status": "REJECTED",
            "reasons": list(r.reasons),
            "schema_version": HANDOFF_SCHEMA_VERSION,
        }
        for r in load_result.rejected
    ]
    all_records.sort(key=lambda rec: rec["input_index"])
    if len(all_records) != load_result.input_count:
        raise RuntimeError("perte silencieuse : décisions ≠ candidats reçus")
    _write_jsonl(output / "decisions.jsonl", all_records)
    _write_jsonl(
        output / "admissible_values.jsonl",
        [r for r in all_records if r["status"] in DOWNSTREAM_ALLOWED_STATUSES],
    )

    report = build_report(
        load_result,
        decisions,
        summary,
        anomalies,
        bilan,
        sample,
        refused,
        inputs={
            "candidates": _relative(candidates),
            "anomalies": _relative(anomalies_path),
            "bilan": _relative(bilan_path),
            "human_decisions": _relative(human_decisions),
        },
    )
    write_json(output / "fqg_report.json", report)
    (output / "fqg_report.md").write_text(report_markdown(report), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        description="Financial Quality Gate — contrôle des candidats AMMC (handoff)"
    )
    cli.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_HANDOFF_DIR / "candidats_fqg.json",
    )
    cli.add_argument(
        "--anomalies", type=Path, default=DEFAULT_HANDOFF_DIR / "anomalies.json"
    )
    cli.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    cli.add_argument("--bilan", type=Path, default=None)
    cli.add_argument("--human-decisions", type=Path, default=None)
    cli.add_argument("--pdf-sources", type=Path, default=DEFAULT_PDF_SOURCES)
    return cli


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report = run(
        args.candidates,
        args.anomalies,
        args.output,
        bilan_path=args.bilan,
        human_decisions=args.human_decisions,
        pdf_sources=args.pdf_sources,
    )
    keys = (
        "candidates_received",
        "structurally_valid",
        "exact_duplicates",
        "value_conflict_groups",
        "verified",
        "rejected",
        "remaining_to_check",
    )
    print(json.dumps({k: report[k] for k in keys}, ensure_ascii=False, indent=2))
    print(f"Rapport : {(args.output / 'fqg_report.md').as_posix()}")


if __name__ == "__main__":
    main()
