"""Adaptation stricte des sorties FQG vers le contrat du warehouse DuckDB.

Le module ne modifie ni les décisions du Financial Quality Gate ni la base.
Il transforme uniquement ``admissible_values.jsonl`` en deux artefacts :

* un CSV d'observations financières ``VERIFIED`` ;
* un registre JSON de références fiables compatible avec ``loader.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from moroccan_markets_ai.data.warehouse.loader import (
    DEFAULT_COMPANIES,
    DEFAULT_MANIFEST,
    PROJECT_ROOT,
    WarehouseValidationError,
    atomic_json,
    manifest_hashes,
    read_csv,
)

DEFAULT_FQG = (
    PROJECT_ROOT
    / "data/quality/ammc/fqg_validation/admissible_values.jsonl"
)
DEFAULT_EXISTING_FINANCIAL = (
    PROJECT_ROOT
    / "data/quality/ammc/data_06_2/verified_financial_observations.csv"
)
DEFAULT_EXISTING_REFERENCES = (
    PROJECT_ROOT / "data/quality/ammc/data_06_2/financial_references.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/quality/ammc/fqg_warehouse"

FINANCIAL_FIELDS = [
    "company_id",
    "exercise_year",
    "indicator",
    "normalized_value",
    "currency",
    "scale",
    "accounting_perimeter",
    "source_pdf_sha256",
    "page_number",
    "table_id",
    "verification_reference_id",
    "verification_status",
]
UNIT_SCALES = {"MAD": 1, "KMAD": 1_000, "MMAD": 1_000_000}
SCOPE_MAP = {"SOCIAL": "SOCIAL", "CONSOLIDE": "CONSOLIDATED"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
CANDIDATE_ID_RE = re.compile(r"[0-9a-f]{16}")


@dataclass(frozen=True)
class AdaptedRecord:
    """Une observation et sa référence warehouse, déjà validées."""

    financial: dict[str, str]
    reference: dict[str, Any]
    document: dict[str, Any]
    business_key: tuple[str, int, int, str, str, str]
    amount: Decimal


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise WarehouseValidationError([f"Champ FQG absent : {context}.{key}"])
    return value


def _decimal(value: Any, field: str) -> Decimal:
    text = str(value).strip()
    if isinstance(value, str):
        text = re.sub(r"[\s\u00a0\u202f]", "", text).replace(",", ".")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise WarehouseValidationError(
            [f"Valeur FQG décimale invalide : {field}={value}"]
        ) from exc
    if not parsed.is_finite():
        raise WarehouseValidationError([f"Valeur FQG non finie : {field}"])
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _integer(value: Any, field: str, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WarehouseValidationError(
            [f"Entier FQG invalide : {field}={value}"]
        ) from exc
    if parsed < minimum:
        raise WarehouseValidationError(
            [f"Entier FQG hors limite : {field}={parsed}"]
        )
    return parsed


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Lit un JSONL sans ignorer les lignes invalides ou non objet."""

    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WarehouseValidationError(
                    [f"JSONL FQG invalide à la ligne {line_number}: {exc.msg}"]
                ) from exc
            if not isinstance(record, dict):
                raise WarehouseValidationError(
                    [f"Objet JSON attendu à la ligne FQG {line_number}"]
                )
            records.append(record)
    return records


def _allowed_company_ids(path: Path) -> set[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    companies = payload.get("companies", [])
    ids = {str(item.get("company_id", "")) for item in companies}
    if len(companies) != 15 or len(ids) != 15 or "" in ids:
        raise WarehouseValidationError(
            ["Le référentiel doit contenir exactement 15 company_id uniques"]
        )
    return ids


def _adapt_record(
    record: dict[str, Any],
    *,
    allowed_companies: set[str],
    available_pdfs: set[tuple[str, int, str]],
) -> AdaptedRecord:
    candidate_id = str(_required(record, "candidate_id", "record"))
    if not CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise WarehouseValidationError(
            [f"candidate_id FQG invalide : {candidate_id}"]
        )
    if record.get("status") != "VERIFIED":
        raise WarehouseValidationError(
            [f"Statut FQG non admissible pour {candidate_id}: {record.get('status')}"]
        )

    normalized = _required(record, "normalized", candidate_id)
    evidence = _required(record, "evidence", candidate_id)
    if not isinstance(normalized, dict) or not isinstance(evidence, dict):
        raise WarehouseValidationError(
            [f"normalized/evidence invalides pour {candidate_id}"]
        )
    normalized_context = f"{candidate_id}.normalized"
    evidence_context = f"{candidate_id}.evidence"

    company = str(_required(normalized, "company", normalized_context))
    if company not in allowed_companies:
        raise WarehouseValidationError([f"Entreprise FQG inconnue : {company}"])
    report_year = _integer(
        _required(normalized, "report_year", normalized_context),
        "report_year",
        minimum=1900,
    )
    observation_year = _integer(
        _required(normalized, "observation_year", normalized_context),
        "observation_year",
        minimum=1900,
    )
    if observation_year > report_year:
        raise WarehouseValidationError(
            [f"Période future interdite pour {candidate_id}"]
        )
    column_year = _integer(
        _required(evidence, "column_year", evidence_context),
        "evidence.column_year",
        minimum=1900,
    )
    if column_year != observation_year:
        raise WarehouseValidationError(
            [f"Année de colonne incohérente pour {candidate_id}"]
        )

    source_value = _decimal(
        _required(normalized, "value", normalized_context), "normalized.value"
    )
    value_read = _decimal(
        _required(evidence, "value_read", evidence_context), "evidence.value_read"
    )
    sign = str(_required(evidence, "sign", evidence_context))
    if sign not in {"+", "-"}:
        raise WarehouseValidationError([f"Signe FQG invalide pour {candidate_id}"])
    signed_read = -abs(value_read) if sign == "-" else abs(value_read)
    if abs(signed_read - source_value) > Decimal("0.005"):
        raise WarehouseValidationError(
            [f"Valeur et preuve divergentes pour {candidate_id}"]
        )

    unit = str(_required(evidence, "unit", evidence_context))
    if unit not in UNIT_SCALES:
        raise WarehouseValidationError([f"Unité FQG invalide pour {candidate_id}"])
    scale = UNIT_SCALES[unit]
    amount = source_value * scale
    currency = str(_required(evidence, "currency", evidence_context))
    if currency != "MAD":
        raise WarehouseValidationError([f"Devise FQG invalide pour {candidate_id}"])
    source_scope = str(_required(evidence, "scope", evidence_context))
    if source_scope not in SCOPE_MAP:
        raise WarehouseValidationError(
            [f"Périmètre FQG invalide pour {candidate_id}"]
        )
    perimeter = SCOPE_MAP[source_scope]

    pdf_sha256 = str(_required(evidence, "pdf_sha256", evidence_context)).lower()
    if not SHA256_RE.fullmatch(pdf_sha256):
        raise WarehouseValidationError([f"SHA-256 FQG invalide pour {candidate_id}"])
    if (company, report_year, pdf_sha256) not in available_pdfs:
        raise WarehouseValidationError(
            [f"PDF FQG absent du manifeste pour {candidate_id}"]
        )
    page = _integer(
        _required(evidence, "pdf_page", evidence_context),
        "evidence.pdf_page",
    )
    source_page = _integer(
        _required(normalized, "source_page", normalized_context),
        "normalized.source_page",
    )
    if page != source_page:
        raise WarehouseValidationError(
            [f"Page et preuve divergentes pour {candidate_id}"]
        )
    table_id = str(_required(normalized, "source_table", normalized_context))
    indicator = str(_required(normalized, "indicator", normalized_context))
    checked_by = str(_required(evidence, "checked_by", evidence_context)).strip()
    checked_at = str(_required(evidence, "checked_at", evidence_context)).strip()
    if not checked_by or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked_at):
        raise WarehouseValidationError(
            [f"Vérificateur ou date invalide pour {candidate_id}"]
        )

    financial = {
        "company_id": company,
        "exercise_year": str(report_year),
        "indicator": indicator,
        "normalized_value": _decimal_text(amount),
        "currency": currency,
        "scale": str(scale),
        "accounting_perimeter": perimeter,
        "source_pdf_sha256": pdf_sha256,
        "page_number": str(page),
        "table_id": table_id,
        "verification_reference_id": candidate_id,
        "verification_status": "VERIFIED",
    }
    reference = {
        "reference_id": candidate_id,
        "indicator": indicator,
        "source_label": normalized.get("label")
        or normalized.get("source_line_text")
        or indicator,
        "reference_value": _decimal_text(source_value),
        "normalized_value": _decimal_text(amount),
        "currency": currency,
        "unit": unit,
        "scale": scale,
        "accounting_perimeter": perimeter,
        "period": str(observation_year),
        "comparative_period": observation_year != report_year,
        "page_number": page,
        "table_id": table_id,
        "reliable": True,
        "expected_status": "VERIFIED",
        "verification_method": "FQG_HUMAN_VERIFIED",
        "verification_evidence": [
            {
                "mode": "HUMAN_PDF_COMPARISON",
                "page_number": page,
                "value_read": _decimal_text(value_read),
                "sign": sign,
                "unit": unit,
                "scope": source_scope,
                "column_year": column_year,
                "checked_by": checked_by,
                "checked_at": checked_at,
            }
        ],
    }
    document = {
        "company_id": company,
        "exercise_year": report_year,
        "source_pdf": str(_required(evidence, "pdf_file", evidence_context)),
        "source_pdf_sha256": pdf_sha256,
    }
    business_key = (
        company,
        report_year,
        observation_year,
        indicator,
        perimeter,
        currency,
    )
    return AdaptedRecord(financial, reference, document, business_key, amount)


def _reference_index(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    references: dict[str, dict[str, Any]] = {}
    for document in payload.get("documents", []):
        for observation in document.get("observations", []):
            reference_id = str(observation.get("reference_id", ""))
            if not reference_id or reference_id in references:
                raise WarehouseValidationError(
                    [f"Référence existante absente ou dupliquée : {reference_id}"]
                )
            references[reference_id] = {
                **observation,
                "company_id": document["company_id"],
                "exercise_year": int(document["exercise_year"]),
            }
    return references


def _existing_business_values(
    financial_path: Path, references_path: Path
) -> tuple[dict[tuple[str, int, int, str, str, str], Decimal], set[str]]:
    _, rows = read_csv(financial_path)
    references = _reference_index(references_path)
    values: dict[tuple[str, int, int, str, str, str], Decimal] = {}
    reference_ids: set[str] = set()
    for row in rows:
        reference_id = row.get("verification_reference_id", "")
        reference = references.get(reference_id)
        if reference is None:
            raise WarehouseValidationError(
                [f"Référence existante introuvable : {reference_id}"]
            )
        key = (
            row["company_id"],
            int(row["exercise_year"]),
            int(reference["period"]),
            row["indicator"],
            row["accounting_perimeter"],
            row["currency"],
        )
        amount = _decimal(row["normalized_value"], "existing.normalized_value")
        previous = values.get(key)
        if previous is not None and previous != amount:
            raise WarehouseValidationError(
                [f"Contradiction déjà présente dans le socle : {key}"]
            )
        values[key] = amount
        reference_ids.add(reference_id)
    return values, reference_ids


def _atomic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FINANCIAL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def adapt_fqg_export(
    *,
    fqg_path: Path,
    financial_output: Path,
    references_output: Path,
    audit_output: Path,
    companies_path: Path = DEFAULT_COMPANIES,
    manifest_path: Path = DEFAULT_MANIFEST,
    existing_financial: Path | None = None,
    existing_references: Path | None = None,
) -> dict[str, Any]:
    """Produit un incrément warehouse strict à partir du JSONL FQG."""

    if (existing_financial is None) != (existing_references is None):
        raise WarehouseValidationError(
            ["Le CSV et les références existants doivent être fournis ensemble"]
        )

    records = read_jsonl(fqg_path)
    allowed_companies = _allowed_company_ids(companies_path)
    available_pdfs = manifest_hashes(manifest_path)
    adapted = [
        _adapt_record(
            record,
            allowed_companies=allowed_companies,
            available_pdfs=available_pdfs,
        )
        for record in records
    ]

    existing_values: dict[tuple[str, int, int, str, str, str], Decimal] = {}
    existing_ids: set[str] = set()
    if existing_financial is not None and existing_references is not None:
        existing_values, existing_ids = _existing_business_values(
            existing_financial, existing_references
        )

    emitted: list[AdaptedRecord] = []
    skipped: list[str] = []
    new_values: dict[tuple[str, int, int, str, str, str], Decimal] = {}
    new_ids: set[str] = set()
    for item in adapted:
        reference_id = item.financial["verification_reference_id"]
        if reference_id in new_ids:
            raise WarehouseValidationError(
                [f"verification_reference_id dupliqué : {reference_id}"]
            )
        old_amount = existing_values.get(item.business_key)
        if reference_id in existing_ids:
            if old_amount == item.amount:
                skipped.append(reference_id)
                continue
            raise WarehouseValidationError(
                [f"verification_reference_id existant incompatible : {reference_id}"]
            )
        if old_amount is not None:
            if old_amount != item.amount:
                raise WarehouseValidationError(
                    [f"Valeur FQG contradictoire avec le socle : {item.business_key}"]
                )
            skipped.append(reference_id)
            continue
        new_amount = new_values.get(item.business_key)
        if new_amount is not None:
            if new_amount != item.amount:
                raise WarehouseValidationError(
                    [f"Conflit entre valeurs FQG : {item.business_key}"]
                )
            skipped.append(reference_id)
            continue
        new_values[item.business_key] = item.amount
        new_ids.add(reference_id)
        emitted.append(item)

    documents: dict[tuple[str, int, str], dict[str, Any]] = {}
    observations: defaultdict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for item in emitted:
        key = (
            item.document["company_id"],
            item.document["exercise_year"],
            item.document["source_pdf_sha256"],
        )
        documents[key] = item.document
        observations[key].append(item.reference)
    reference_payload = {
        "schema_version": "1.0",
        "source": "FQG admissible_values.jsonl",
        "documents": [
            {**documents[key], "observations": observations[key]}
            for key in sorted(documents)
        ],
    }
    audit = {
        "schema_version": "1.0",
        "status": "ADAPTED",
        "fqg_input": str(Path(fqg_path)),
        "input_records": len(records),
        "emitted_records": len(emitted),
        "skipped_existing_duplicates": len(skipped),
        "skipped_reference_ids": skipped,
        "unit_counts": {
            unit: sum(
                item.reference["unit"] == unit for item in emitted
            )
            for unit in UNIT_SCALES
        },
        "financial_output": str(Path(financial_output)),
        "references_output": str(Path(references_output)),
    }
    _atomic_csv(financial_output, [item.financial for item in emitted])
    atomic_json(references_output, reference_payload)
    atomic_json(audit_output, audit)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fqg", type=Path, default=DEFAULT_FQG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--existing-financial", type=Path, default=DEFAULT_EXISTING_FINANCIAL
    )
    parser.add_argument(
        "--existing-references", type=Path, default=DEFAULT_EXISTING_REFERENCES
    )
    parser.add_argument(
        "--without-existing",
        action="store_true",
        help="Ne compare pas la sortie FQG à un socle warehouse existant.",
    )
    args = parser.parse_args()
    existing_financial = None if args.without_existing else args.existing_financial
    existing_references = None if args.without_existing else args.existing_references
    audit = adapt_fqg_export(
        fqg_path=args.fqg,
        financial_output=args.output / "verified_financial_observations.csv",
        references_output=args.output / "financial_references.json",
        audit_output=args.output / "fqg_adapter_report.json",
        companies_path=args.companies,
        manifest_path=args.manifest,
        existing_financial=existing_financial,
        existing_references=existing_references,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
