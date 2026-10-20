from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "extracted" / "ammc" / "ocr"
RENDER_DPI = 400


@dataclass(frozen=True)
class OcrRegion:
    """Zone de page et schéma nécessaires à une extraction financière ciblée."""

    name: str
    page_no: int
    side: str
    columns: tuple[str, ...]
    label_limit: float
    column_boundaries: tuple[float, ...]
    start_marker: str
    end_marker: str


S2M_SOCIAL_REGIONS = (
    OcrRegion(
        name="bilan_actif_social",
        page_no=8,
        side="left",
        columns=(
            "RUBRIQUE",
            "BRUT",
            "AMORTISSEMENTS_ET_PROVISIONS",
            "30/06/2026_NET",
            "31/12/2025_NET",
        ),
        label_limit=0.42,
        column_boundaries=(0.52, 0.64, 0.77),
        start_marker="IMMOBILISATIONS EN NON VALEURS",
        end_marker="TOTAL GENERAL",
    ),
    OcrRegion(
        name="bilan_passif_social",
        page_no=8,
        side="right",
        columns=("RUBRIQUE", "30/06/2026_NET", "31/12/2025_NET"),
        label_limit=0.55,
        column_boundaries=(0.70,),
        start_marker="FONDS PROPRES",
        end_marker="TOTAL GENERAL",
    ),
    OcrRegion(
        name="cpc_social",
        page_no=9,
        side="left",
        columns=("NATURE", "30/06/2026", "30/06/2025"),
        label_limit=0.52,
        column_boundaries=(0.67,),
        start_marker="PRODUITS D'EXPLOITATION",
        end_marker="RESULTAT NET",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tesseract_version(executable: str) -> str:
    result = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()[0].strip()


def _render_region(
    document: pymupdf.Document,
    region: OcrRegion,
    destination: Path,
) -> None:
    if region.page_no < 1 or region.page_no > document.page_count:
        raise ValueError(
            f"Page {region.page_no} absente du PDF "
            f"({document.page_count} pages)."
        )

    page = document[region.page_no - 1]
    midpoint = page.rect.x0 + page.rect.width / 2
    if region.side == "left":
        clip = pymupdf.Rect(
            page.rect.x0,
            page.rect.y0,
            midpoint,
            page.rect.y1,
        )
    elif region.side == "right":
        clip = pymupdf.Rect(
            midpoint,
            page.rect.y0,
            page.rect.x1,
            page.rect.y1,
        )
    else:
        raise ValueError(f"Côté de page inconnu : {region.side}")

    pixmap = page.get_pixmap(dpi=RENDER_DPI, clip=clip, alpha=False)
    pixmap.save(destination)


def _run_tesseract_tsv(image: Path, executable: str) -> str:
    result = subprocess.run(
        [
            executable,
            str(image),
            "stdout",
            "-l",
            "fra+eng",
            "--psm",
            "6",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _canonical_amount(value: str) -> str:
    cleaned = re.sub(r"[^0-9,.\- ]", "", value).strip(" .,:;|[]")
    if not cleaned or not re.search(r"\d", cleaned):
        return "-" if "-" in cleaned else ""

    negative = cleaned.lstrip().startswith("-")
    cleaned = cleaned.replace("-", "").replace(" ", "")
    if "," not in cleaned and "." not in cleaned:
        number = f"{int(cleaned):,}".replace(",", " ")
        return ("-" if negative else "") + number

    separator = "," if "," in cleaned else "."
    integer, decimals = cleaned.rsplit(separator, maxsplit=1)
    if (
        not integer
        or not integer.isdigit()
        or not decimals.isdigit()
        or len(decimals) != 2
    ):
        return value.strip()
    formatted = f"{int(integer):,}".replace(",", " ")
    return f"{'-' if negative else ''}{formatted},{decimals}"


def _line_words(tsv: str) -> list[list[dict[str, str]]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    reader = csv.DictReader(
        io.StringIO(tsv),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
    )
    for word in reader:
        if word.get("level") != "5" or not word.get("text", "").strip():
            continue
        key = (
            word["page_num"],
            word["block_num"],
            word["par_num"],
            word["line_num"],
        )
        grouped.setdefault(key, []).append(word)
    return list(grouped.values())


def _column_index(position: float, boundaries: tuple[float, ...]) -> int:
    for index, boundary in enumerate(boundaries):
        if position < boundary:
            return index
    return len(boundaries)


def _extract_dataframe(tsv: str, region: OcrRegion) -> pd.DataFrame:
    extracted: list[list[str]] = []
    in_table = False
    tsv_rows = list(
        csv.DictReader(
            io.StringIO(tsv),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        )
    )
    page_row = next(row for row in tsv_rows if row.get("level") == "1")
    page_width = max(int(page_row["width"]), 1)

    for words in _line_words(tsv):
        complete_line = " ".join(word["text"] for word in words)
        normalized_line = complete_line.upper().replace("’", "'")

        if region.start_marker in normalized_line:
            in_table = True
        if not in_table:
            continue

        cells: list[list[str]] = [[] for _ in region.columns]
        for word in words:
            center = (
                int(word["left"]) + int(word["width"]) / 2
            ) / page_width
            if center < region.label_limit:
                cell_index = 0
            else:
                cell_index = 1 + _column_index(
                    center,
                    region.column_boundaries,
                )
                cell_index = min(cell_index, len(cells) - 1)
            cells[cell_index].append(word["text"])

        row = [" ".join(cells[0]).strip()]
        row.extend(_canonical_amount(" ".join(cell)) for cell in cells[1:])
        if row[0]:
            first_character = row[0].lstrip("*|\" ")[:1]
            if (
                first_character.islower()
                and not any(row[1:])
                and extracted
                and not any(extracted[-1][1:])
            ):
                extracted[-1][0] = f"{extracted[-1][0]} {row[0]}"
            else:
                extracted.append(row)

        if region.end_marker in normalized_line:
            break

    return pd.DataFrame(extracted, columns=region.columns)


def _amount(value: str) -> Decimal | None:
    normalized = value.replace(" ", "").replace(",", ".")
    if not normalized or normalized == "-":
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _find_row(dataframe: pd.DataFrame, marker: str) -> pd.Series | None:
    labels = dataframe.iloc[:, 0].str.upper()
    matches = dataframe[labels.str.contains(marker, regex=False)]
    return None if matches.empty else matches.iloc[-1]


def _validate_financial_tables(
    tables: dict[str, pd.DataFrame],
) -> tuple[str, list[dict[str, str]]]:
    checks: list[dict[str, str]] = []

    def add_check(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    actif = tables["bilan_actif_social"]
    passif = tables["bilan_passif_social"]
    cpc = tables["cpc_social"]
    add_check(
        "actif_structure",
        actif.shape[0] >= 40 and actif.shape[1] == 5,
        str(actif.shape),
    )
    add_check(
        "passif_structure",
        passif.shape[0] >= 35 and passif.shape[1] == 3,
        str(passif.shape),
    )
    add_check(
        "cpc_structure",
        cpc.shape[0] >= 30 and cpc.shape[1] == 3,
        str(cpc.shape),
    )

    actif_total = _find_row(actif, "TOTAL GENERAL")
    passif_total = _find_row(passif, "TOTAL GENERAL")
    cpc_result = _find_row(cpc, "RESULTAT NET")
    totals_found = all(
        row is not None for row in (actif_total, passif_total, cpc_result)
    )
    add_check(
        "critical_rows_found",
        totals_found,
        "Actif, Passif et résultat net",
    )

    if actif_total is not None and passif_total is not None:
        current_equal = _amount(str(actif_total.iloc[3])) == _amount(
            str(passif_total.iloc[1])
        )
        prior_equal = _amount(str(actif_total.iloc[4])) == _amount(
            str(passif_total.iloc[2])
        )
        add_check(
            "bilan_current_balanced",
            current_equal,
            f"{actif_total.iloc[3]} / {passif_total.iloc[1]}",
        )
        add_check(
            "bilan_prior_balanced",
            prior_equal,
            f"{actif_total.iloc[4]} / {passif_total.iloc[2]}",
        )
    else:
        add_check("bilan_current_balanced", False, "Total général absent")
        add_check("bilan_prior_balanced", False, "Total général absent")

    if cpc_result is not None:
        result_values = [_amount(str(value)) for value in cpc_result.iloc[1:]]
        add_check(
            "cpc_result_values",
            all(value is not None for value in result_values),
            str(list(cpc_result.iloc[1:])),
        )
    else:
        add_check("cpc_result_values", False, "Résultat net absent")

    expected_values = {
        "actif_total": (
            actif_total,
            [
                "414 461 058,80",
                "86 845 499,75",
                "317 569 112,54",
                "297 032 298,41",
            ],
        ),
        "passif_total": (
            passif_total,
            ["317 569 112,54", "297 032 298,41"],
        ),
        "cpc_result": (
            cpc_result,
            ["11 950 822,05", "11 198 861,84"],
        ),
    }
    for name, (row, expected) in expected_values.items():
        observed = [] if row is None else list(row.iloc[1:])
        add_check(
            f"{name}_matches_pdf",
            observed == expected,
            f"observé={observed}; attendu={expected}",
        )

    status = (
        "PARSED"
        if all(check["status"] == "PASS" for check in checks)
        else "PARTIAL"
    )
    return status, checks


def recover_social_financial_tables(
    pdf_path: Path,
    output_root: Path = OUTPUT_ROOT,
) -> dict:
    """Récupère les états sociaux S2M après découpage des doubles pages."""

    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise RuntimeError("Tesseract CLI est introuvable dans le PATH.")

    output_dir = Path(output_root) / pdf_path.stem / "targeted_financial_tables"
    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, pd.DataFrame] = {}
    region_metadata: list[dict[str, object]] = []

    with pymupdf.open(pdf_path) as document, tempfile.TemporaryDirectory() as temporary:
        temporary_dir = Path(temporary)
        for region in S2M_SOCIAL_REGIONS:
            image = temporary_dir / f"{region.name}.png"
            _render_region(document, region, image)
            dataframe = _extract_dataframe(
                _run_tesseract_tsv(image, tesseract),
                region,
            )
            tables[region.name] = dataframe
            csv_name = f"{region.name}.csv"
            dataframe.to_csv(tables_dir / csv_name, index=False)
            region_metadata.append(
                {
                    "name": region.name,
                    "source_page": region.page_no,
                    "crop": region.side,
                    "rows": len(dataframe),
                    "columns": len(dataframe.columns),
                    "csv": f"tables/{csv_name}",
                }
            )

    status, checks = _validate_financial_tables(tables)
    summary = {
        "source_pdf": str(pdf_path),
        "source_sha256": _sha256(pdf_path),
        "source_pages": sorted(
            {region.page_no for region in S2M_SOCIAL_REGIONS}
        ),
        "profile": "s2m_social_financial_statements",
        "ocr_engine": "tesseract-cli",
        "tesseract_version": _tesseract_version(tesseract),
        "ocr_languages": ["fra", "eng"],
        "render_dpi": RENDER_DPI,
        "tables_detected": len(tables),
        "tables": region_metadata,
        "checks": checks,
        "validation_scope": (
            "Structure, totaux critiques comparés au PDF et équilibre "
            "Actif/Passif. Pas de validation comptable exhaustive."
        ),
        "accounting_validation": "NOT_PERFORMED",
        "status": status,
    }
    (output_dir / "ocr_metadata.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def recover_pages(
    pdf_path: Path,
    start_page: int,
    end_page: int,
    output_root: Path = OUTPUT_ROOT,
) -> dict:
    """Compatibilité avec l'ancienne commande de récupération des pages 8-9."""

    if (start_page, end_page) != (8, 9):
        raise ValueError(
            "Le profil ciblé S2M accepte uniquement --start-page 8 "
            "--end-page 9."
        )
    return recover_social_financial_tables(pdf_path, output_root)


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("pdf", type=Path)
    cli.add_argument("--start-page", type=int)
    cli.add_argument("--end-page", type=int)
    cli.add_argument(
        "--profile",
        choices=["s2m-social-statements"],
        default="s2m-social-statements",
    )
    args = cli.parse_args()
    if (args.start_page is None) != (args.end_page is None):
        cli.error("--start-page et --end-page doivent être fournis ensemble.")
    if args.start_page is not None:
        summary = recover_pages(
            args.pdf,
            args.start_page,
            args.end_page,
        )
    else:
        summary = recover_social_financial_tables(args.pdf)
    print(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
