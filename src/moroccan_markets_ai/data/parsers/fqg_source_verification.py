"""Vérification des candidats contre leurs sources (FQG, jour 4).

1. ``worksheet`` : sélectionne un échantillon représentatif (≥ 30 candidats) et
   génère une grille de vérification + un fichier de décisions humaines vide.
2. La personne qui vérifie ouvre le PDF et remplit ``human_decisions.json``.
3. ``apply`` : valide chaque décision (Pydantic) et produit
   ``verified_sample.json``, ``rejected_sample.json`` et ``review_required.json``.

VERIFIED et PDF_CONFIRMED ne viennent QUE d'une décision humaine accompagnée
d'une preuve complète ; une décision incomplète est refusée, jamais devinée.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from moroccan_markets_ai.data.parsers.fqg_candidate_checks import (
    CandidateDecision,
    run_checks,
)
from moroccan_markets_ai.data.parsers.fqg_handoff_loader import (
    DEFAULT_HANDOFF_DIR,
    PROJECT_ROOT,
    load_anomalies,
    load_candidates,
    read_json,
)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "quality" / "ammc" / "fqg_validation"
DEFAULT_PDF_SOURCES = (
    PROJECT_ROOT / "config" / "quality" / "ammc" / "fqg_pdf_sources.json"
)
DEFAULT_EXTRACTED_ROOT = PROJECT_ROOT / "data" / "extracted" / "ammc"
# Paquet de passation du Parsing : <racine>/<entreprise>/<année>/{original,tables}
DEFAULT_HANDOVER_ROOT = PROJECT_ROOT / "data" / "raw" / "ammc" / "AMMC_annual_45"

# Un rapport par entreprise ayant des candidats, les 3 exercices représentés.
SAMPLE_REPORTS: tuple[tuple[str, int], ...] = (
    ("attijariwafa_bank", 2025),
    ("wafa_assurance", 2023),
    ("auto_hall", 2024),
    ("cosumar", 2025),
    ("lafargeholcim_maroc", 2023),
    ("lesieur_cristal", 2024),
    ("managem", 2023),
    ("marsa_maroc", 2023),
    ("s2m", 2025),
    ("sonasid", 2023),
    ("sothema", 2025),
)
PER_REPORT = 3
MINIMUM_SAMPLE = 30
HUMAN_STATUSES = ("VERIFIED", "PDF_CONFIRMED", "REJECTED", "REVIEW_REQUIRED")


# ---------------------------------------------------------------- échantillon


def select_sample(
    decisions: list[CandidateDecision],
    reports: tuple[tuple[str, int], ...] = SAMPLE_REPORTS,
    per_report: int = PER_REPORT,
    minimum: int = MINIMUM_SAMPLE,
) -> list[CandidateDecision]:
    """Échantillon déterministe : indicateurs variés, statuts mélangés, ≥ minimum."""
    by_report: dict[tuple[str, int], list[CandidateDecision]] = defaultdict(list)
    for d in decisions:
        by_report[(d.c.entreprise, d.c.report_year)].append(d)

    chosen: list[CandidateDecision] = []
    for report in reports:
        pool = sorted(by_report.get(report, []), key=lambda d: d.loaded.candidate_id)
        picked: list[CandidateDecision] = []
        # 1) un REVIEW_REQUIRED et un NOT_CHECKED si possible
        for status in ("REVIEW_REQUIRED", "NOT_CHECKED"):
            for d in pool:
                if d.status == status and d not in picked:
                    picked.append(d)
                    break
        # 2) compléter en privilégiant des indicateurs encore absents
        for d in pool:
            if len(picked) >= per_report:
                break
            if d not in picked and d.c.indicator not in {p.c.indicator for p in picked}:
                picked.append(d)
        for d in pool:
            if len(picked) >= per_report:
                break
            if d not in picked:
                picked.append(d)
        chosen.extend(picked[:per_report])

    if len(chosen) < minimum:  # compléter hors des rapports prévus si besoin
        rest = sorted(
            (d for d in decisions if d not in chosen),
            key=lambda d: d.loaded.candidate_id,
        )
        chosen.extend(rest[: minimum - len(chosen)])
    return chosen


def build_pdf_sources(ocr_root: Path) -> dict[str, Any]:
    """Construit le registre PDF/SHA-256 depuis les ``ocr_metadata.json`` du Parsing."""
    reports: dict[tuple[str, int], dict[str, Any]] = {}
    for meta_path in sorted(Path(ocr_root).glob("*/*/ocr_metadata.json")):
        meta = read_json(meta_path)
        source = str(meta.get("source_pdf", "")).replace("\\", "/")
        if "/annual/" not in source:
            continue
        company, year, name = source.split("/annual/")[1].split("/")[-3:]
        reports[(company, int(year))] = {
            "company": company,
            "report_year": int(year),
            "pdf_file": name,
            "pdf_relative_path": f"data/raw/ammc/pdfs/annual/{company}/{year}/{name}",
            "pdf_sha256": meta["source_sha256"],
        }
    return {
        "schema_version": "1.0",
        "source": "ocr_metadata.json (Parsing & OCR)",
        "reports": list(reports.values()),
    }


def load_pdf_sources(path: Path = DEFAULT_PDF_SOURCES) -> dict[tuple[str, int], dict]:
    if not Path(path).exists():
        return {}
    data = read_json(path)
    return {(r["company"], r["report_year"]): r for r in data["reports"]}


# ---------------------------------------------------------------- contrôles automatiques

UNIT_PATTERNS = (
    ("MMAD", r"EN MILLIONS DE (?:DIRHAMS|DHS?|MAD)|\bMMAD\b|\bMDH\b"),
    ("KMAD", r"EN MILLI+ERS DE (?:DIRHAMS|DHS?|MAD)|\bKMAD\b|\bKDH\b"),
    ("MAD", r"\(\s*EN DIRHAMS\s*\)|EN DIRHAMS|\bEN DH\b|\bEN MAD\b"),
)
SOCIAL_RE = re.compile(
    r"COMPTES SOCIAUX|COMPTE DE PRODUITS ET CHARGES|EXERCICE PRECEDENT"
)
CONSOLIDATED_RE = re.compile(r"CONSOLID|IFRS|PART DU GROUPE|SOCIETE MERE")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# Valeur juste mais indicateur mal classé : à reclasser, pas à valider tel quel.
CLASSIFICATION_CODES = frozenset(
    {
        "LABEL_INDICATOR_MISMATCH",
        "EQUITY_GROUP_SHARE",
        "NET_INCOME_GROUP_SHARE_CONFUSION",
        "MERGED_LABEL",
        "INDICATOR_PROFILE_MISMATCH",
    }
)


def _fold(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[ \t\u00a0\u202f]+", " ", text).upper()


def _value_variants(value: Decimal) -> set[str]:
    """Écritures possibles d'un montant dans un rapport marocain."""
    amount = f"{abs(value):.2f}"
    integer, decimals = amount.split(".")
    grouped = f"{int(integer):,}"
    forms = {grouped.replace(",", " "), grouped.replace(",", "."), integer}
    if decimals != "00":
        forms |= {f"{form},{decimals}" for form in list(forms)}
    return forms


def detect_unit(text: str) -> list[str]:
    lines = [line for line in _fold(text).splitlines() if "PAR ACTION" not in line]
    folded = "\n".join(lines)
    return [unit for unit, pattern in UNIT_PATTERNS if re.search(pattern, folded)]


def detect_scope(text: str) -> list[str]:
    folded = _fold(text)
    scopes = []
    if CONSOLIDATED_RE.search(folded):
        scopes.append("CONSOLIDE")
    if SOCIAL_RE.search(folded):
        scopes.append("SOCIAL")
    return scopes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_rows(path: Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def _column_year(rows: list[list[str]], column: int, report_year: int) -> int | None:
    """Année de la colonne d'après les 3 premières lignes d'en-tête."""
    for row in rows[:3]:
        if column >= len(row):
            continue
        header = _fold(row[column])
        years = {int(y) for y in YEAR_RE.findall(header)}
        if len(years) == 1:
            return years.pop()
        if "PRECEDENT" in header:
            return report_year - 1
        if "EXERCICE" in header and not years:
            return report_year
    return None


def auto_check(
    d: CandidateDecision,
    pdf_source: dict | None,
    handover_root: Path = DEFAULT_HANDOVER_ROOT,
) -> dict[str, Any]:
    """Compare le candidat au PDF, au CSV et à unified_document.json.

    Le résultat est un INDICE pour la personne qui vérifie : il ne change
    jamais le statut et n'invente aucune unité ni aucun périmètre.
    """
    c = d.c
    report_dir = Path(handover_root) / c.entreprise / str(c.report_year)
    result: dict[str, Any] = {"report_dir_found": report_dir.exists()}
    if not report_dir.exists() or not pdf_source:
        result["proposal"] = "SOURCES_NOT_AVAILABLE"
        return result

    # --- PDF : empreinte + présence de la valeur sur la page citée
    pdf_path = report_dir / "original" / pdf_source["pdf_file"]
    page_text = ""
    if pdf_path.exists():
        sha = _sha256(pdf_path)
        result["pdf_sha256"] = sha
        result["pdf_sha256_ok"] = sha == pdf_source["pdf_sha256"]
        import pymupdf

        with pymupdf.open(pdf_path) as doc:
            if 1 <= c.source_page <= len(doc):
                page_text = doc[c.source_page - 1].get_text()
    flat = re.sub(r"[\u00a0\u202f]", " ", page_text)
    result["value_on_pdf_page"] = any(v in flat for v in _value_variants(c.valeur))

    # --- CSV Docling : ligne, colonne et année de colonne
    number = c.source_table.split("_")[-1]
    csv_path = report_dir / "tables" / f"table_{number}.csv"
    csv_title = ""
    result["csv_row_found"] = False
    if csv_path.exists():
        rows = _csv_rows(csv_path)
        csv_title = " ".join(" ".join(r) for r in rows[:2])
        label = _fold(c.nom).strip()[:20]
        for index in (c.source_ligne + 1, c.source_ligne):
            if index < len(rows) and label and label in _fold(" ".join(rows[index])):
                result["csv_row_found"] = True
                result["csv_row"] = " | ".join(cell.strip() for cell in rows[index])
                variants = _value_variants(c.valeur)
                for column, cell in enumerate(rows[index]):
                    cell_flat = re.sub(r"[\u00a0\u202f]", " ", cell).strip()
                    if (
                        cell_flat in variants
                        or cell_flat.replace(" , ", ",") in variants
                    ):
                        result["csv_column"] = column
                        result["column_year"] = _column_year(
                            rows, column, c.report_year
                        )
                        break
                break
        result["table_title"] = (rows[0][0].strip() if rows and rows[0] else "")[:120]

    # --- unified_document.json : la table existe-t-elle, sur quelle page ?
    unified = report_dir / "unified_document.json"
    if unified.exists():
        tables = read_json(unified).get("tables", [])
        match = next((t for t in tables if t.get("table_id") == c.source_table), None)
        result["unified_table_found"] = match is not None
        if match:
            pages = match.get("page") or []
            pages = pages if isinstance(pages, list) else [pages]
            result["unified_pages"] = pages
            result["unified_page_ok"] = c.source_page in pages
            if csv_path.exists():
                result["unified_csv_sha256_ok"] = match.get("sha256") == _sha256(
                    csv_path
                )

    # --- indices d'unité et de périmètre (page + titre du tableau)
    units = detect_unit(csv_title) or detect_unit(page_text)
    scopes = detect_scope(csv_title) or detect_scope(page_text)
    result["unit_hint"] = units[0] if len(units) == 1 else None
    result["unit_candidates"] = units
    result["scope_hint"] = scopes[0] if len(scopes) == 1 else None
    result["scope_candidates"] = scopes

    # --- proposition (jamais appliquée automatiquement)
    reason_codes = {r["code"] for r in d.reasons}
    sources_ok = (
        result.get("pdf_sha256_ok")
        and result["value_on_pdf_page"]
        and result["csv_row_found"]
    )
    if reason_codes & {"THOUSANDS_SEPARATOR_MISREAD", "VALUE_NOT_IN_SOURCE_LINE"}:
        result["proposal"] = "PROPOSE_REJECTED"
        result["proposal_note"] = "valeur mal convertie par l'extraction"
    elif not sources_ok:
        result["proposal"] = "PROPOSE_REVIEW_REQUIRED"
        result["proposal_note"] = "source introuvable ou différente"
    elif reason_codes & CLASSIFICATION_CODES:
        result["proposal"] = "PROPOSE_REVIEW_REQUIRED"
        result["proposal_note"] = (
            "montant présent dans le PDF mais indicateur à reclasser : "
            + ", ".join(sorted(reason_codes & CLASSIFICATION_CODES))
        )
    elif (
        result["unit_hint"]
        and result["scope_hint"]
        and result.get("column_year") == c.annee
    ):
        result["proposal"] = "PROPOSE_VERIFIED"
    else:
        result["proposal"] = "PROPOSE_PDF_CONFIRMED"
    return result


# ---------------------------------------------------------------- grille


def build_worksheet(
    sample: list[CandidateDecision],
    pdf_sources: dict[tuple[str, int], dict],
    handover_root: Path = DEFAULT_HANDOVER_ROOT,
) -> list[dict[str, Any]]:
    rows = []
    for n, d in enumerate(sample, start=1):
        c = d.c
        pdf = pdf_sources.get((c.entreprise, c.report_year), {})
        rows.append(
            {
                "n": n,
                "candidate_id": d.loaded.candidate_id,
                "company": c.entreprise,
                "report_year": c.report_year,
                "observation_year": c.annee,
                "indicator": c.indicator,
                "label": c.nom,
                "value_extracted": str(c.valeur),
                "source_table": c.source_table,
                "source_page": c.source_page,
                "source_row": c.source_ligne,
                "source_line_text": c.ligne_source,
                "automatic_status": d.status,
                "automatic_reasons": [
                    r["code"] for r in d.reasons if r["severity"] != "INFO"
                ],
                "pdf_file": pdf.get("pdf_file"),
                "pdf_relative_path": pdf.get("pdf_relative_path"),
                "pdf_sha256_expected": pdf.get("pdf_sha256"),
                "auto_check": auto_check(d, pdf or None, handover_root),
                "what_to_check": (
                    f"Ouvrir {pdf.get('pdf_file', 'le PDF')} page {c.source_page} : "
                    f"trouver « {c.nom.strip()} », colonne {c.annee}. "
                    "Relever montant, signe, unité (MAD/KMAD/MMAD) et périmètre "
                    "(SOCIAL/CONSOLIDE)."
                ),
            }
        )
    return rows


def human_decision_template(worksheet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Décision VIDE, preuve pré-remplie avec les indices automatiques.

    La personne qui vérifie doit regarder la page, corriger si besoin, puis
    remplir ``decision``, ``reason``, ``checked_by`` et ``checked_at``.
    """
    template = []
    for row in worksheet:
        auto = row.get("auto_check", {})
        template.append(
            {
                "candidate_id": row["candidate_id"],
                "n": row["n"],
                "reminder": f"{row['company']} {row['report_year']} – "
                f"{row['label'].strip()} ({row['observation_year']}) = "
                f"{row['value_extracted']}",
                "proposal": auto.get("proposal"),
                "decision": None,
                "reason": "",
                "checked_by": "",
                "checked_at": "",
                "evidence": {
                    "pdf_file": row["pdf_file"],
                    "pdf_sha256": auto.get("pdf_sha256", ""),
                    "pdf_page": row["source_page"],
                    "printed_page": None,
                    "table_title": auto.get("table_title", ""),
                    "value_read": "",
                    "sign": "",
                    "unit": auto.get("unit_hint") or "",
                    "scope": auto.get("scope_hint") or "",
                    "column_year": auto.get("column_year"),
                    "currency": "MAD",
                },
            }
        )
    return template


# ---------------------------------------------------------------- décisions humaines


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_file: str | None = None
    pdf_sha256: str | None = None
    pdf_page: int | None = Field(default=None, ge=1)
    printed_page: int | None = None
    table_title: str | None = None
    value_read: Decimal | None = None
    sign: Literal["+", "-"] | None = None
    unit: Literal["MAD", "KMAD", "MMAD"] | None = None
    scope: Literal["SOCIAL", "CONSOLIDE"] | None = None
    column_year: int | None = None
    currency: Literal["MAD"] | None = "MAD"

    @field_validator(
        "pdf_file",
        "pdf_sha256",
        "table_title",
        "value_read",
        "sign",
        "unit",
        "scope",
        mode="before",
    )
    @classmethod
    def _blank(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        return value

    @field_validator("value_read", mode="before")
    @classmethod
    def _french_number(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = re.sub(r"[\s\u00a0\u202f]", "", value).replace(",", ".")
        return value

    @field_validator("pdf_sha256")
    @classmethod
    def _sha(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("SHA-256 attendu (64 caractères hexadécimaux)")
        return value.lower() if value else value


class HumanDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_id: str
    decision: Literal["VERIFIED", "PDF_CONFIRMED", "REJECTED", "REVIEW_REQUIRED"] | None
    reason: str = ""
    checked_by: str = ""
    checked_at: date | None = None
    evidence: Evidence = Field(default_factory=Evidence)

    @field_validator("checked_at", mode="before")
    @classmethod
    def _blank_date(cls, value: Any) -> Any:
        return None if value in ("", None) else value


def _missing(evidence: Evidence, fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if getattr(evidence, f) in (None, "")]


def validate_decision(
    human: HumanDecision,
    d: CandidateDecision,
    pdf_source: dict | None,
) -> list[str]:
    """Retourne la liste des problèmes ; vide = décision acceptable."""
    problems: list[str] = []
    if human.decision is None:
        return ["décision non remplie"]
    if not human.checked_by.strip() or human.checked_at is None:
        problems.append("checked_by et checked_at obligatoires")
    if not human.reason.strip():
        problems.append("raison obligatoire")
    ev = human.evidence
    value = d.c.valeur

    if human.decision in ("VERIFIED", "PDF_CONFIRMED"):
        problems += [
            f"preuve manquante : {f}"
            for f in _missing(ev, ("pdf_file", "pdf_page", "value_read", "sign"))
        ]
        if ev.value_read is not None:
            signed = -abs(ev.value_read) if ev.sign == "-" else abs(ev.value_read)
            if abs(signed - value) > Decimal("0.005"):
                problems.append(
                    f"valeur lue {signed} ≠ valeur extraite {value} : "
                    "utiliser REJECTED (valeur discordante)"
                )
    if human.decision == "VERIFIED":
        problems += [
            f"preuve manquante : {f}"
            for f in _missing(ev, ("pdf_sha256", "unit", "scope", "column_year"))
        ]
        if ev.column_year is not None and ev.column_year != d.c.annee:
            problems.append(f"colonne {ev.column_year} ≠ année observée {d.c.annee}")
        expected = (pdf_source or {}).get("pdf_sha256")
        if ev.pdf_sha256 and expected and ev.pdf_sha256 != expected:
            problems.append("SHA-256 du PDF différent de celui du corpus")
    return problems


def apply_human_decisions(
    decisions: list[CandidateDecision],
    human_rows: list[dict[str, Any]],
    pdf_sources: dict[tuple[str, int], dict],
) -> list[dict[str, Any]]:
    """Applique les décisions valides ; retourne les décisions refusées (avec motif)."""
    by_id = {d.loaded.candidate_id: d for d in decisions}
    refused: list[dict[str, Any]] = []
    for row in human_rows:
        try:
            human = HumanDecision.model_validate(row)
        except ValidationError as exc:
            refused.append(
                {"candidate_id": row.get("candidate_id"), "problems": [str(exc)]}
            )
            continue
        d = by_id.get(human.candidate_id)
        if d is None:
            refused.append(
                {"candidate_id": human.candidate_id, "problems": ["candidat inconnu"]}
            )
            continue
        if human.decision is None:
            continue  # pas encore vérifié : on ne touche à rien
        problems = validate_decision(
            human, d, pdf_sources.get((d.c.entreprise, d.c.report_year))
        )
        if problems:
            refused.append({"candidate_id": human.candidate_id, "problems": problems})
            continue
        d.status = human.decision
        d.evidence = {
            **human.evidence.model_dump(mode="json"),
            "checked_by": human.checked_by,
            "checked_at": human.checked_at.isoformat() if human.checked_at else None,
        }
        d.reasons.append(
            {
                "code": f"HUMAN_{human.decision}",
                "severity": "HUMAN",
                "message": human.reason,
            }
        )
    return refused


# ---------------------------------------------------------------- sorties


def _provenance(d: CandidateDecision) -> dict[str, Any]:
    out = d.to_dict()
    return {
        "candidate_id": out["candidate_id"],
        "status": out["status"],
        "normalized": out["normalized"],
        "reasons": out["reasons"],
        "evidence": out["evidence"],
        "conflict_group": out["conflict_group"],
        "original": out["original"],
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_sample_outputs(
    output_dir: Path, sample: list[CandidateDecision], refused: list[dict[str, Any]]
) -> dict[str, int]:
    verified = [
        _provenance(d) for d in sample if d.status in ("VERIFIED", "PDF_CONFIRMED")
    ]
    rejected = [_provenance(d) for d in sample if d.status == "REJECTED"]
    review = [
        _provenance(d) for d in sample if d.status in ("REVIEW_REQUIRED", "NOT_CHECKED")
    ]
    write_json(output_dir / "verified_sample.json", verified)
    write_json(output_dir / "rejected_sample.json", rejected)
    write_json(output_dir / "review_required.json", review)
    write_json(output_dir / "refused_human_decisions.json", refused)
    return {
        "sample": len(sample),
        "verified_or_pdf_confirmed": len(verified),
        "rejected": len(rejected),
        "still_to_review": len(review),
        "refused_human_decisions": len(refused),
    }


def run_pipeline(candidates: Path, anomalies: Path):
    result = load_candidates(candidates)
    decisions, summary = run_checks(result.loaded, load_anomalies(anomalies))
    return result, decisions, summary


def main() -> None:
    cli = argparse.ArgumentParser(description="FQG jour 4 : vérification des sources")
    cli.add_argument("action", choices=["sources", "worksheet", "apply"])
    cli.add_argument(
        "--candidates", type=Path, default=DEFAULT_HANDOFF_DIR / "candidats_fqg.json"
    )
    cli.add_argument(
        "--anomalies", type=Path, default=DEFAULT_HANDOFF_DIR / "anomalies.json"
    )
    cli.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    cli.add_argument("--pdf-sources", type=Path, default=DEFAULT_PDF_SOURCES)
    cli.add_argument("--extracted-root", type=Path, default=DEFAULT_EXTRACTED_ROOT)
    cli.add_argument("--handover-root", type=Path, default=DEFAULT_HANDOVER_ROOT)
    args = cli.parse_args()

    if args.action == "sources":
        registry = build_pdf_sources(args.extracted_root / "ocr_generic")
        write_json(args.pdf_sources, registry)
        print(f"{len(registry['reports'])} rapports -> {args.pdf_sources.as_posix()}")
        return

    _, decisions, _ = run_pipeline(args.candidates, args.anomalies)
    sample = select_sample(decisions)
    sources = load_pdf_sources(args.pdf_sources)
    human_path = args.output / "human_decisions.json"

    if args.action == "worksheet":
        sheet = build_worksheet(sample, sources, args.handover_root)
        write_json(args.output / "verification_worksheet.json", sheet)
        if human_path.exists():
            print(f"{human_path} existe déjà : non écrasé.")
        else:
            write_json(human_path, human_decision_template(sheet))
        print(
            json.dumps(
                {
                    "sample": len(sample),
                    "companies": len({r["company"] for r in sheet}),
                    "years": sorted({r["report_year"] for r in sheet}),
                    "indicators": sorted({r["indicator"] for r in sheet}),
                    "proposals": {
                        p: sum(1 for r in sheet if r["auto_check"].get("proposal") == p)
                        for p in sorted(
                            {r["auto_check"].get("proposal") for r in sheet}
                        )
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    human_rows = read_json(human_path)
    refused = apply_human_decisions(decisions, human_rows, sources)
    counts = write_sample_outputs(args.output, sample, refused)
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
