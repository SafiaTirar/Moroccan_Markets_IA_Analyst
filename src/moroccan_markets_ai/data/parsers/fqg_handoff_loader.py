"""Chargeur et validation structurelle des candidats financiers AMMC (handoff FQG).

Lit ``candidats_fqg.json`` produit par l'Extraction et valide chaque candidat
avec Pydantic. Aucun candidat n'est perdu silencieusement : chaque entrée est
soit chargée, soit rejetée avec une raison explicite.

Voir ``docs/fqg_ammc_integration_contract.md``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_HANDOFF_DIR = PROJECT_ROOT / "data" / "quality" / "ammc" / "handoff_fqg_45"
DEFAULT_REGISTRY = (
    PROJECT_ROOT / "config" / "quality" / "ammc" / "financial_indicator_registry.json"
)
DEFAULT_CORPUS = PROJECT_ROOT / "config" / "data" / "ammc_corpus.json"

HANDOFF_SCHEMA_VERSION = "fqg-handoff-1.0"
REPORT_YEARS = frozenset({2023, 2024, 2025})
MIN_OBSERVATION_YEAR = 2000
INPUT_ALLOWED_STATUSES = frozenset({"NOT_CHECKED"})
ALL_STATUSES = (
    "NOT_CHECKED",
    "REVIEW_REQUIRED",
    "PDF_CONFIRMED",
    "VERIFIED",
    "REJECTED",
)
KNOWN_FIELDS = frozenset(
    {
        "nom",
        "annee",
        "valeur",
        "ligne_source",
        "entreprise",
        "unite",
        "perimetre",
        "source_table",
        "source_page",
        "source_ligne",
        "validation_status",
        "indicator",
        "report_year",
    }
)


def read_json(path: Path) -> Any:
    """Lit un JSON en UTF-8 (indispensable sous Windows)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


class HandoffCandidate(BaseModel):
    """Candidat tel que transmis par l'Extraction (noms de champs d'origine)."""

    model_config = ConfigDict(extra="allow", strict=False)

    entreprise: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    indicator: str = Field(min_length=1)
    annee: int
    report_year: int
    valeur: Decimal
    ligne_source: str
    source_table: str = Field(min_length=1)
    source_page: int = Field(ge=1)
    source_ligne: int = Field(ge=0)
    validation_status: str
    unite: str | None = None
    perimetre: str | None = None

    @field_validator(
        "annee", "report_year", "source_page", "source_ligne", mode="before"
    )
    @classmethod
    def _strict_int(cls, value: Any) -> Any:
        # bool est un int en Python et un float 2023.5 serait tronqué : on refuse.
        if isinstance(value, bool) or (
            isinstance(value, float) and not value.is_integer()
        ):
            raise ValueError("entier attendu")
        if isinstance(value, str):
            raise ValueError("entier attendu, chaîne reçue")  # noqa: TRY004 (Pydantic)
        return value

    @field_validator("valeur", mode="before")
    @classmethod
    def _numeric_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            raise ValueError("valeur numérique attendue")
        if isinstance(value, float):
            # repr() évite les artefacts binaires : 85.712 reste "85.712".
            value = repr(value)
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"valeur non numérique : {value!r}") from exc
        if not number.is_finite():
            raise ValueError("valeur non finie (NaN/Infinity)")
        return number

    @field_validator("unite", "perimetre", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@dataclass(frozen=True)
class LoadedCandidate:
    candidate_id: str
    input_index: int
    original: dict[str, Any]
    candidate: HandoffCandidate
    profile: str | None
    unknown_fields: tuple[str, ...]

    def normalized(self) -> dict[str, Any]:
        c = self.candidate
        return {
            "company": c.entreprise,
            "indicator": c.indicator,
            "label": c.nom,
            "observation_year": c.annee,
            "report_year": c.report_year,
            "value": str(c.valeur),
            "unit": c.unite,
            "scope": c.perimetre,
            "source_table": c.source_table,
            "source_page": c.source_page,
            "source_row": c.source_ligne,
            "source_line_text": c.ligne_source,
            "profile": self.profile,
        }


@dataclass(frozen=True)
class RejectedRecord:
    input_index: int
    original: Any
    reasons: tuple[dict[str, str], ...]
    candidate_id: str | None = None


@dataclass
class LoadResult:
    input_count: int
    loaded: list[LoadedCandidate] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)

    def check_no_silent_loss(self) -> None:
        handled = len(self.loaded) + len(self.rejected)
        if handled != self.input_count:
            raise RuntimeError(
                f"Perte silencieuse : {self.input_count} reçus, {handled} traités"
            )


def candidate_id(record: dict[str, Any]) -> str:
    """Identifiant stable (16 hex) indépendant de l'ordre du fichier."""
    parts = [
        record.get("entreprise"),
        record.get("report_year"),
        record.get("indicator"),
        record.get("annee"),
        record.get("source_table"),
        record.get("source_page"),
        record.get("source_ligne"),
        record.get("valeur"),
    ]
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_reference_sets(
    registry_path: Path = DEFAULT_REGISTRY, corpus_path: Path = DEFAULT_CORPUS
) -> tuple[set[str], dict[str, str], set[str]]:
    """Retourne (codes d'indicateurs, profils par entreprise, entreprises du corpus)."""
    registry = read_json(registry_path)
    indicators = {item["indicator"] for item in registry["indicators"]}
    profiles = dict(registry.get("company_profiles", {}))
    corpus = read_json(corpus_path)
    companies = {item["company_id"] for item in corpus["companies"]}
    return indicators, profiles, companies


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": "ERROR", "message": message}


def validate_record(
    index: int,
    record: Any,
    indicators: set[str],
    profiles: dict[str, str],
    companies: set[str],
) -> LoadedCandidate | RejectedRecord:
    if not isinstance(record, dict):
        return RejectedRecord(
            index,
            record,
            (_reason("NOT_AN_OBJECT", "le candidat n'est pas un objet JSON"),),
        )
    cid = candidate_id(record)
    try:
        cand = HandoffCandidate.model_validate(record)
    except ValidationError as exc:
        reasons = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "?"
            code = "MISSING_FIELD" if err["type"] == "missing" else "INVALID_FIELD"
            if loc == "valeur" and code == "INVALID_FIELD":
                code = "NON_NUMERIC_VALUE"
            reasons.append(_reason(code, f"{loc} : {err['msg']}"))
        return RejectedRecord(index, record, tuple(reasons), cid)

    reasons: list[dict[str, str]] = []
    if cand.entreprise not in companies:
        reasons.append(
            _reason("UNKNOWN_COMPANY", f"entreprise inconnue : {cand.entreprise}")
        )
    if cand.indicator not in indicators:
        reasons.append(
            _reason("UNKNOWN_INDICATOR", f"indicateur inconnu : {cand.indicator}")
        )
    if cand.report_year not in REPORT_YEARS:
        reasons.append(
            _reason(
                "INVALID_REPORT_YEAR", f"report_year hors corpus : {cand.report_year}"
            )
        )
    if not MIN_OBSERVATION_YEAR <= cand.annee <= cand.report_year:
        reasons.append(
            _reason(
                "INVALID_OBSERVATION_YEAR",
                f"annee {cand.annee} hors [{MIN_OBSERVATION_YEAR}, {cand.report_year}]",
            )
        )
    if cand.validation_status not in INPUT_ALLOWED_STATUSES:
        reasons.append(
            _reason(
                "STATUS_NOT_ALLOWED",
                f"statut d'entrée {cand.validation_status!r} interdit (NOT_CHECKED attendu)",
            )
        )
    if reasons:
        return RejectedRecord(index, record, tuple(reasons), cid)

    unknown = tuple(sorted(set(record) - KNOWN_FIELDS))
    return LoadedCandidate(
        candidate_id=cid,
        input_index=index,
        original=dict(record),
        candidate=cand,
        profile=profiles.get(cand.entreprise),
        unknown_fields=unknown,
    )


def load_candidates(
    candidates_path: Path = DEFAULT_HANDOFF_DIR / "candidats_fqg.json",
    registry_path: Path = DEFAULT_REGISTRY,
    corpus_path: Path = DEFAULT_CORPUS,
) -> LoadResult:
    """Charge et valide tous les candidats ; lève une erreur si le fichier n'est pas une liste."""
    data = read_json(Path(candidates_path))
    if not isinstance(data, list):
        raise TypeError(f"{candidates_path} : une liste JSON est attendue")
    indicators, profiles, companies = load_reference_sets(registry_path, corpus_path)
    result = LoadResult(input_count=len(data))
    for index, record in enumerate(data):
        outcome = validate_record(index, record, indicators, profiles, companies)
        if isinstance(outcome, LoadedCandidate):
            result.loaded.append(outcome)
        else:
            result.rejected.append(outcome)
    result.check_no_silent_loss()
    return result


def load_anomalies(
    path: Path = DEFAULT_HANDOFF_DIR / "anomalies.json",
) -> list[dict[str, Any]]:
    data = read_json(Path(path))
    if not isinstance(data, list):
        raise TypeError(f"{path} : une liste JSON est attendue")
    return data


def load_bilan(path: Path = DEFAULT_HANDOFF_DIR / "bilan.json") -> list[dict[str, Any]]:
    data = read_json(Path(path))
    if not isinstance(data, list):
        raise TypeError(f"{path} : une liste JSON est attendue")
    return data


def main() -> None:
    import argparse

    cli = argparse.ArgumentParser(description="Chargement structurel des candidats FQG")
    cli.add_argument(
        "--candidates", type=Path, default=DEFAULT_HANDOFF_DIR / "candidats_fqg.json"
    )
    cli.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    cli.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = cli.parse_args()
    result = load_candidates(args.candidates, args.registry, args.corpus)
    summary = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "received": result.input_count,
        "structurally_valid": len(result.loaded),
        "rejected": len(result.rejected),
        "rejections": [
            {
                "input_index": r.input_index,
                "candidate_id": r.candidate_id,
                "reasons": r.reasons,
            }
            for r in result.rejected
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()