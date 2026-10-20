
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "integrated" / "ammc"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_csv(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise FileNotFoundError(path)

    df = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
    )

    if df.empty or not df.astype(str).apply(
        lambda col: col.str.strip().ne("")
    ).any().any():
        raise ValueError(f"Tableau vide : {path}")

    return df.shape


def integrate(
    docling_metadata: Path,
    ocr_metadata: Path,
    output_root: Path = OUTPUT_ROOT,
) -> dict:
    docling_metadata = Path(docling_metadata).resolve()
    ocr_metadata = Path(ocr_metadata).resolve()

    base = read_json(docling_metadata)
    ocr = read_json(ocr_metadata)

    # Les deux extractions doivent provenir du même PDF.
    source = Path(base["source_pdf"])
    source_sha = base["sha256"]

    if not source.is_file():
        raise FileNotFoundError(source)

    if file_hash(source) != source_sha:
        raise ValueError("PDF original modifié")

    if ocr["source_sha256"] != source_sha:
        raise ValueError("OCR provenant d'un autre PDF")

    if Path(ocr["source_pdf"]).resolve() != source.resolve():
        raise ValueError("Sources PDF différentes")

    if ocr["status"] != "PARSED":
        raise ValueError("OCR non validé")

    if not ocr.get("checks") or any(
        check["status"] != "PASS"
        for check in ocr["checks"]
    ):
        raise ValueError("Contrôles OCR incomplets")

    docling_dir = docling_metadata.parent
    ocr_dir = ocr_metadata.parent

    records = []
    invalid_tables = []

    validation = base["validation"]

    # 1. Charger les tableaux Docling validés.
    for table in validation["tables"]:
        index = table["table_index"]
        status = table["status"]

        if status == "INVALID":
            invalid_tables.append(table)
            continue

        csv_path = (
            docling_dir
            / "tables"
            / f"table_{index:03d}.csv"
        )

        rows, columns = validate_csv(csv_path)

        records.append({
            "table_id": f"DOCLING_{index:03d}",
            "source": "DOCLING",
            "page": table["pages"],
            "table_type": "UNKNOWN",
            "reporting_scope": "UNKNOWN",
            "unit": "UNKNOWN",
            "csv_path": str(csv_path),
            "rows": rows,
            "columns": columns,
            "quality_status": status,
            "sha256": file_hash(csv_path),
        })

    # 2. Ajouter les tableaux OCR validés.
    ocr_records = []

    for table in ocr["tables"]:
        relative_path = Path(table["csv"])

        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("Chemin OCR invalide")

        csv_path = (ocr_dir / relative_path).resolve()

        if not csv_path.is_relative_to(ocr_dir):
            raise ValueError("CSV hors du dossier OCR")

        rows, columns = validate_csv(csv_path)

        if (rows, columns) != (
            table["rows"],
            table["columns"],
        ):
            raise ValueError("Dimensions OCR incohérentes")

        record = {
            "table_id": f"OCR_{table['name']}",
            "source": "OCR",
            "page": [table["source_page"]],
            "table_type": table["name"],
            "reporting_scope": "SOCIAL",
            "unit": "UNKNOWN",
            "csv_path": str(csv_path),
            "rows": rows,
            "columns": columns,
            "quality_status": "REVIEW_REQUIRED",
            "sha256": file_hash(csv_path),
        }

        ocr_records.append(record)

    records.extend(ocr_records)

    # 3. Documenter les tableaux remplacés logiquement.
    replacements = []

    for invalid in invalid_tables:
        pages = set(invalid["pages"])

        candidates = [
            record["table_id"]
            for record in ocr_records
            if pages.intersection(record["page"])
        ]

        replacements.append({
            "docling_table": invalid["table_index"],
            "pages": invalid["pages"],
            "ocr_candidates": candidates,
            "status": (
                "OCR_AVAILABLE_FOR_REVIEW"
                if candidates
                else "UNRESOLVED"
            ),
        })

    # 4. Conserver le statut global PARTIAL.
    # Les anomalies restantes nécessitent une revue.
    result = {
        "source_pdf": str(source),
        "source_sha256": source_sha,
        "docling_status": base["status"],
        "ocr_status": ocr["status"],
        "integration_status": "PARTIAL",
        "active_table_count": len(records),
        "tables": records,
        "replacements": replacements,
        "remaining_docling_issues": [
            issue for issue in validation["issues"]
            if issue.get("table_index") not in {
                item["table_index"]
                for item in invalid_tables
            }
        ],
    }

    output_dir = Path(output_root) / (
        f"{source.stem}_{source_sha[:12]}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "unified_document.json"
    temporary_path = output_dir / "unified_document.json.tmp"

    temporary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temporary_path.replace(output_path)

    print(f"Tables actives : {len(records)}")
    print(f"Document intégré : {output_path}")

    return result


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--docling", type=Path, required=True)
    cli.add_argument("--ocr", type=Path, required=True)

    args = cli.parse_args()

    integrate(
        docling_metadata=args.docling,
        ocr_metadata=args.ocr,
    )


if __name__ == "__main__":
    main()
