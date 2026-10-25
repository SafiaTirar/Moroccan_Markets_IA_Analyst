from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "integrated" / "ammc"
INTEGRATION_SCHEMA_VERSION = "2.2"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"Objet JSON attendu : {path}")
    return loaded


def validate_csv(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise FileNotFoundError(path)
    dataframe = pd.read_csv(path, dtype=str, keep_default_na=False)
    if dataframe.empty or not dataframe.astype(str).map(str.strip).ne("").any().any():
        raise ValueError(f"Tableau vide : {path}")
    return dataframe.shape


def _source_identity(metadata: dict) -> tuple[Path, str]:
    source = Path(metadata["source_pdf"]).resolve()
    sha = metadata.get("source_sha256", metadata.get("sha256"))
    if not isinstance(sha, str):
        raise TypeError("SHA-256 source absent")
    if not source.is_file() or file_hash(source) != sha:
        raise ValueError("PDF original absent ou modifié")
    return source, sha


def _load_docling_tables(metadata_path: Path, base: dict) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    invalid: list[dict] = []
    validation = base.get("validation", {})
    for table in validation.get("tables", []):
        if table["status"] == "INVALID":
            invalid.append(table)
            continue
        index = table["table_index"]
        csv_path = metadata_path.parent / "tables" / f"table_{index:03d}.csv"
        rows, columns = validate_csv(csv_path)
        records.append(
            {
                "table_id": f"DOCLING_{index:03d}",
                "source": "DOCLING",
                "page": table.get("pages", []),
                "bbox": None,
                "table_type": "UNKNOWN",
                "reporting_scope": "UNKNOWN",
                "unit": "UNKNOWN",
                "csv_path": str(csv_path.resolve()),
                "coordinates_path": None,
                "rows": rows,
                "columns": columns,
                "quality_status": table["status"],
                "financial_validation": "NOT_PERFORMED",
                "sha256": file_hash(csv_path),
            }
        )
    return records, invalid


def _ocr_record(metadata_path: Path, table: dict) -> dict:
    relative = Path(table["csv"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Chemin OCR invalide")
    ocr_dir = metadata_path.parent.resolve()
    csv_path = (ocr_dir / relative).resolve()
    if not csv_path.is_relative_to(ocr_dir):
        raise ValueError("CSV OCR hors du dossier OCR")
    rows, columns = validate_csv(csv_path)
    expected = (table.get("rows"), table.get("columns"))
    if None not in expected and (rows, columns) != expected:
        raise ValueError(f"Dimensions OCR incohérentes : {csv_path}")

    coordinate_path = table.get("coordinates")
    resolved_coordinates = None
    coordinates_sha256 = None
    if coordinate_path:
        relative_coordinates = Path(coordinate_path)
        if (
            relative_coordinates.is_absolute()
            or ".." in relative_coordinates.parts
        ):
            raise ValueError("Chemin de coordonnées OCR invalide")
        coordinates = (ocr_dir / relative_coordinates).resolve()
        if not coordinates.is_relative_to(ocr_dir) or not coordinates.is_file():
            raise ValueError("Coordonnées OCR absentes ou hors du dossier OCR")
        resolved_coordinates = str(coordinates)
        coordinates_sha256 = file_hash(coordinates)
    page = table.get("page", table.get("source_page"))
    name = table.get("name") or table.get("table_id") or csv_path.stem
    return {
        "table_id": f"OCR_{name}" if not str(name).startswith("OCR_") else name,
        "source": "OCR",
        "page": [int(page)] if page is not None else [],
        "bbox": table.get("bbox"),
        "table_type": table.get("name", "UNKNOWN"),
        "reporting_scope": table.get("reporting_scope", "UNKNOWN"),
        "unit": table.get("unit", "UNKNOWN"),
        "csv_path": str(csv_path),
        "coordinates_path": resolved_coordinates,
        "coordinates_sha256": coordinates_sha256,
        "rows": rows,
        "columns": columns,
        "quality_status": table.get(
            "quality_status",
            "TECHNICALLY_RECOVERED",
        ),
        "financial_validation": table.get(
            "financial_validation",
            "NOT_PERFORMED",
        ),
        "sha256": file_hash(csv_path),
    }


def _validate_ocr_artifacts(metadata_path: Path, metadata: dict) -> None:
    root = metadata_path.parent.resolve()
    artifacts = metadata.get("artifact_sha256")
    if artifacts is None:
        return
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Manifeste d'intégrité OCR invalide")
    for relative, expected in artifacts.items():
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("Chemin d'artefact OCR invalide")
        path = (root / relative_path).resolve()
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or file_hash(path) != expected
        ):
            raise ValueError(f"Artefact OCR absent ou modifié : {relative}")


def _deduplicate_ocr(records: list[dict]) -> list[dict]:
    selected: list[dict] = []
    seen_content: set[tuple[tuple[int, ...], str]] = set()
    for record in records:
        identity = (tuple(record["page"]), record["sha256"])
        if identity in seen_content:
            continue
        seen_content.add(identity)
        selected.append(record)
    return selected


def _integration_fingerprint(
    docling_metadata: Path | None,
    ocr_metadata: Path | None,
) -> str:
    payload = {
        "schema": INTEGRATION_SCHEMA_VERSION,
        "docling": file_hash(docling_metadata) if docling_metadata else None,
        "ocr": file_hash(ocr_metadata) if ocr_metadata else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _cached_result(path: Path, fingerprint: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        result = read_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if result.get("integration_fingerprint") != fingerprint:
        return None
    for table in result.get("tables", []):
        csv_path = Path(table["csv_path"])
        if not csv_path.is_file() or file_hash(csv_path) != table["sha256"]:
            return None
        if coordinate_path := table.get("coordinates_path"):
            coordinates = Path(coordinate_path)
            if (
                not coordinates.is_file()
                or file_hash(coordinates) != table.get("coordinates_sha256")
            ):
                return None
    result["cache_used"] = True
    return result


def integrate(
    docling_metadata: Path | None = None,
    ocr_metadata: Path | None = None,
    output_root: Path = OUTPUT_ROOT,
) -> dict:
    if docling_metadata is None and ocr_metadata is None:
        raise ValueError("Au moins une source Docling ou OCR est requise")
    docling_path = Path(docling_metadata).resolve() if docling_metadata else None
    ocr_path = Path(ocr_metadata).resolve() if ocr_metadata else None
    base = read_json(docling_path) if docling_path else None
    ocr = read_json(ocr_path) if ocr_path else None

    identities = [
        _source_identity(metadata)
        for metadata in (base, ocr)
        if metadata is not None
    ]
    source, source_sha = identities[0]
    if any(item != identities[0] for item in identities[1:]):
        raise ValueError("Les extractions ne proviennent pas du même PDF")
    if ocr and ocr.get("status") not in {"PARSED", "PARTIAL"}:
        raise ValueError("OCR inutilisable")
    if ocr and ocr_path:
        _validate_ocr_artifacts(ocr_path, ocr)

    fingerprint = _integration_fingerprint(docling_path, ocr_path)
    output_dir = Path(output_root) / f"{source.stem}_{source_sha[:12]}"
    output_path = output_dir / "unified_document.json"
    cached = _cached_result(output_path, fingerprint)
    if cached is not None:
        return cached

    docling_records: list[dict] = []
    invalid_tables: list[dict] = []
    if base and docling_path:
        docling_records, invalid_tables = _load_docling_tables(docling_path, base)

    ocr_records = (
        _deduplicate_ocr(
            [_ocr_record(ocr_path, table) for table in ocr.get("tables", [])]
        )
        if ocr and ocr_path
        else []
    )
    records = [*docling_records, *ocr_records]
    replacements = []
    for invalid in invalid_tables:
        pages = set(invalid.get("pages", []))
        candidates = [
            record["table_id"]
            for record in ocr_records
            if pages.intersection(record["page"])
        ]
        replacements.append(
            {
                "docling_table": invalid["table_index"],
                "pages": invalid.get("pages", []),
                "ocr_candidates": candidates,
                "status": "OCR_AVAILABLE_FOR_REVIEW" if candidates else "UNRESOLVED",
            }
        )

    remaining_issues = []
    if base:
        invalid_indexes = {table["table_index"] for table in invalid_tables}
        remaining_issues = [
            issue
            for issue in base.get("validation", {}).get("issues", [])
            if issue.get("table_index") not in invalid_indexes
        ]
    unresolved = any(item["status"] == "UNRESOLVED" for item in replacements)
    integration_status = (
        "FAILED"
        if not records
        else "PARTIAL"
        if unresolved or remaining_issues or ocr_records
        else "PARSED"
    )
    result: dict[str, Any] = {
        "schema_version": INTEGRATION_SCHEMA_VERSION,
        "source_pdf": str(source),
        "source_sha256": source_sha,
        "docling_status": base.get("status") if base else "NOT_RUN",
        "ocr_status": ocr.get("status") if ocr else "NOT_RUN",
        "integration_status": integration_status,
        "integration_fingerprint": fingerprint,
        "cache_used": False,
        "active_table_count": len(records),
        "tables": records,
        "replacements": replacements,
        "remaining_docling_issues": remaining_issues,
        "financial_validation": "NOT_PERFORMED",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return result


def main() -> None:
    cli = argparse.ArgumentParser(description="Intégration Docling/OCR")
    cli.add_argument("--docling", type=Path)
    cli.add_argument("--ocr", type=Path)
    args = cli.parse_args()
    result = integrate(args.docling, args.ocr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
