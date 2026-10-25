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
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "extracted" / "ammc" / "ocr_generic"
OCR_SCHEMA_VERSION = "3.0"

FINANCIAL_KEYWORDS = (
    "actif",
    "passif",
    "charges",
    "chiffre d'affaires",
    "compte de produits",
    "exercice",
    "produits",
    "resultat",
    "résultat",
    "tresorerie",
    "trésorerie",
)
AMOUNT_END_RE = re.compile(r"[.,]\d{2}[^\d]*$")
NUMERIC_PIECE_RE = re.compile(r"^[\[\](){}|+\-–—.:,\d ]+$")


@dataclass(frozen=True)
class OcrConfig:
    languages: tuple[str, ...] = ("fra", "eng")
    psms: tuple[int, ...] = (6, 11)
    render_dpi: int = 300
    minimum_image_width: int = 400
    minimum_image_height: int = 250
    minimum_region_area_ratio: float = 0.03
    minimum_amount_count: int = 5
    column_tolerance: float = 0.045

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema_version": OCR_SCHEMA_VERSION,
            **asdict(self),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()


@dataclass(frozen=True)
class RegionSelection:
    page_no: int
    bbox: tuple[float, float, float, float]
    source: str
    source_id: str
    name: str | None = None


@dataclass(frozen=True)
class AmountCell:
    value: str
    bbox: tuple[float, float, float, float]
    right_ratio: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _canonical_amount(value: str) -> str:
    cleaned = re.sub(r"[^0-9,.+\- ]", "", value).strip(" .,:;+|[](){}")
    if not cleaned or not re.search(r"\d", cleaned):
        return "-" if "-" in cleaned else ""

    negative = cleaned.lstrip().startswith("-")
    cleaned = cleaned.replace("-", "").replace("+", "").replace(" ", "")
    separators = [index for index, char in enumerate(cleaned) if char in ",."]
    decimal_at = separators[-1] if separators else -1
    has_decimals = decimal_at >= 0 and len(cleaned) - decimal_at - 1 == 2

    if has_decimals:
        integer = re.sub(r"[,.]", "", cleaned[:decimal_at])
        decimals = cleaned[decimal_at + 1 :]
    else:
        integer = re.sub(r"[,.]", "", cleaned)
        decimals = ""

    if not integer.isdigit() or (decimals and not decimals.isdigit()):
        return value.strip()

    formatted = f"{int(integer):,}".replace(",", " ")
    sign = "-" if negative else ""
    return f"{sign}{formatted},{decimals}" if decimals else f"{sign}{formatted}"


def _amount(value: str) -> Decimal | None:
    normalized = value.replace(" ", "").replace(",", ".")
    if not normalized or normalized == "-":
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _tesseract_version(executable: str) -> str:
    result = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()[0].strip()


def _parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        bounds = part.strip().split("-", maxsplit=1)
        if len(bounds) == 1:
            pages.add(int(bounds[0]))
        else:
            pages.update(range(int(bounds[0]), int(bounds[1]) + 1))
    return sorted(pages)


def _pages_from_parser_metadata(path: Path) -> list[int]:
    metadata = json.loads(Path(path).read_text(encoding="utf-8"))
    validation = metadata.get("validation", {})
    declared = validation.get("ocr_required_pages", [])
    if declared:
        return sorted({int(page) for page in declared})

    relevant_codes = {
        "EMPTY_TABLE",
        "FINANCIAL_IMAGE_REQUIRES_OCR",
        "IMAGE_TABLE_REQUIRES_OCR",
    }
    return sorted(
        {
            int(page)
            for issue in validation.get("issues", [])
            if issue.get("code") in relevant_codes
            for page in issue.get("pages", [])
        }
    )


def _automatic_pages(document: pymupdf.Document) -> list[int]:
    pages: list[int] = []
    for page_no, page in enumerate(document, start=1):
        text = page.get_text().strip().casefold()
        has_financial_text = any(word in text for word in FINANCIAL_KEYWORDS)
        large_images = [
            image
            for image in page.get_image_info(xrefs=True)
            if image.get("width", 0) >= 400
            and image.get("height", 0) >= 250
        ]
        if len(text) < 50 or (has_financial_text and large_images):
            pages.append(page_no)
    return pages


def _iou(first: pymupdf.Rect, second: pymupdf.Rect) -> float:
    intersection = first & second
    if intersection.is_empty:
        return 0.0
    union = first.get_area() + second.get_area() - intersection.get_area()
    return intersection.get_area() / union if union else 0.0


def discover_regions(
    document: pymupdf.Document,
    pages: list[int],
    config: OcrConfig,
) -> list[RegionSelection]:
    regions: list[RegionSelection] = []
    for page_no in pages:
        if page_no < 1 or page_no > document.page_count:
            continue
        page = document[page_no - 1]
        page_area = page.rect.get_area()
        page_regions: list[RegionSelection] = []
        for ordinal, image in enumerate(page.get_image_info(xrefs=True), 1):
            bbox = pymupdf.Rect(image["bbox"]) & page.rect
            if (
                image.get("width", 0) < config.minimum_image_width
                or image.get("height", 0) < config.minimum_image_height
                or bbox.get_area() / page_area
                < config.minimum_region_area_ratio
            ):
                continue
            if any(
                _iou(bbox, pymupdf.Rect(item.bbox)) > 0.95
                for item in page_regions
            ):
                continue
            page_regions.append(
                RegionSelection(
                    page_no=page_no,
                    bbox=tuple(bbox),
                    source="embedded_image",
                    source_id=f"xref:{image.get('xref', 0)}:{ordinal}",
                )
            )

        if not page_regions:
            page_regions.append(
                RegionSelection(
                    page_no=page_no,
                    bbox=tuple(page.rect),
                    source="rendered_page",
                    source_id=f"page:{page_no}",
                )
            )
        elif len(page_regions) > 1:
            filtered: list[RegionSelection] = []
            for candidate in page_regions:
                candidate_rect = pymupdf.Rect(candidate.bbox)
                contains_smaller = any(
                    other is not candidate
                    and candidate_rect.get_area()
                    > pymupdf.Rect(other.bbox).get_area() * 1.5
                    and (
                        candidate_rect & pymupdf.Rect(other.bbox)
                    ).get_area()
                    / pymupdf.Rect(other.bbox).get_area()
                    > 0.9
                    for other in page_regions
                )
                if not contains_smaller:
                    filtered.append(candidate)
            page_regions = filtered
        regions.extend(page_regions)
    return regions


def _regions_from_json(path: Path, document: pymupdf.Document) -> list[RegionSelection]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    regions: list[RegionSelection] = []
    for index, item in enumerate(raw, 1):
        page_no = int(item["page"])
        page = document[page_no - 1]
        bbox = item.get("bbox", [0, 0, 1, 1])
        if item.get("normalized", True):
            bbox = [
                bbox[0] * page.rect.width,
                bbox[1] * page.rect.height,
                bbox[2] * page.rect.width,
                bbox[3] * page.rect.height,
            ]
        regions.append(
            RegionSelection(
                page_no=page_no,
                bbox=tuple(float(value) for value in bbox),
                source="configured_region",
                source_id=f"config:{index}",
                name=item.get("name"),
            )
        )
    return regions


def _render_region(
    document: pymupdf.Document,
    region: RegionSelection,
    destination: Path,
    dpi: int,
) -> tuple[int, int]:
    page = document[region.page_no - 1]
    pixmap = page.get_pixmap(
        dpi=dpi,
        clip=pymupdf.Rect(region.bbox),
        alpha=False,
    )
    pixmap.save(destination)
    return pixmap.width, pixmap.height


def _run_tesseract(
    image: Path,
    executable: str,
    config: OcrConfig,
    psm: int,
) -> str:
    result = subprocess.run(
        [
            executable,
            str(image),
            "stdout",
            "-l",
            "+".join(config.languages),
            "--psm",
            str(psm),
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _tsv_lines(tsv: str) -> tuple[int, int, list[list[dict[str, str]]]]:
    rows = list(
        csv.DictReader(
            io.StringIO(tsv),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        )
    )
    page_row = next((row for row in rows if row.get("level") == "1"), None)
    width = int(page_row["width"]) if page_row else 1
    height = int(page_row["height"]) if page_row else 1
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for word in rows:
        if word.get("level") != "5" or not word.get("text", "").strip():
            continue
        key = tuple(
            word[field]
            for field in ("page_num", "block_num", "par_num", "line_num")
        )
        grouped.setdefault(key, []).append(word)
    lines = [
        sorted(words, key=lambda word: int(word["left"]))
        for words in grouped.values()
    ]
    return max(width, 1), max(height, 1), lines


def _page_bbox(
    word_bbox: tuple[float, float, float, float],
    region: RegionSelection,
    image_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    left, top, right, bottom = word_bbox
    image_width, image_height = image_size
    region_rect = pymupdf.Rect(region.bbox)
    scale_x = region_rect.width / image_width
    scale_y = region_rect.height / image_height
    return (
        region_rect.x0 + left * scale_x,
        region_rect.y0 + top * scale_y,
        region_rect.x0 + right * scale_x,
        region_rect.y0 + bottom * scale_y,
    )


def _line_amounts(
    words: list[dict[str, str]],
    image_size: tuple[int, int],
    region: RegionSelection,
) -> tuple[str, list[AmountCell], dict[str, Any]]:
    cells: list[AmountCell] = []
    used: set[int] = set()
    pending: list[tuple[int, str]] = []
    image_width, image_height = image_size

    def flush_pending(items: list[tuple[int, str]]) -> None:
        if not items:
            return
        raw = " ".join(item[1] for item in items)
        decimal_amount = AMOUNT_END_RE.search(items[-1][1]) is not None
        digit_parts = [
            re.sub(r"\D", "", item[1])
            for item in items
            if re.search(r"\d", item[1])
        ]
        integer_amount = (
            not decimal_amount
            and (
                len(digit_parts) >= 2
                or (len(digit_parts) == 1 and len(digit_parts[0]) >= 5)
            )
        )
        if not decimal_amount and not integer_amount:
            return
        value = _canonical_amount(raw)
        if _amount(value) is None:
            return
        selected = [words[item[0]] for item in items]
        left = min(int(item["left"]) for item in selected)
        top = min(int(item["top"]) for item in selected)
        right = max(
            int(item["left"]) + int(item["width"]) for item in selected
        )
        bottom = max(
            int(item["top"]) + int(item["height"]) for item in selected
        )
        cells.append(
            AmountCell(
                value=value,
                bbox=_page_bbox(
                    (left, top, right, bottom),
                    region,
                    image_size,
                ),
                right_ratio=right / image_width,
            )
        )
        used.update(item[0] for item in items)

    for index, word in enumerate(words):
        token = word["text"].strip()
        numeric_token = re.sub(
            r"^[^0-9+\-–—]+|[^0-9.,]+$",
            "",
            token,
        )
        is_numeric = (
            bool(numeric_token)
            and NUMERIC_PIECE_RE.fullmatch(numeric_token) is not None
            and re.search(r"\d|[-–—]", numeric_token) is not None
        )
        if is_numeric:
            if pending:
                previous = words[pending[-1][0]]
                previous_right = int(previous["left"]) + int(previous["width"])
                gap = int(word["left"]) - previous_right
                sign_only = all(
                    not re.search(r"\d", item[1]) for item in pending
                )
                maximum_gap = 0.15 if sign_only else 0.02
                if gap > image_width * maximum_gap:
                    flush_pending(pending)
                    pending = []
            pending.append((index, numeric_token))
            if AMOUNT_END_RE.search(numeric_token):
                flush_pending(pending)
                pending = []
        else:
            flush_pending(pending)
            pending = []
    flush_pending(pending)

    sign_words = [
        (index, word)
        for index, word in enumerate(words)
        if index not in used and word["text"].strip() in {"-", "–", "—"}
    ]
    for sign_index, sign_word in sign_words:
        sign_bbox = _page_bbox(
            (
                int(sign_word["left"]),
                int(sign_word["top"]),
                int(sign_word["left"]) + int(sign_word["width"]),
                int(sign_word["top"]) + int(sign_word["height"]),
            ),
            region,
            image_size,
        )
        candidates = [
            (cell_index, cell)
            for cell_index, cell in enumerate(cells)
            if not cell.value.startswith("-")
            and cell.bbox[0] >= sign_bbox[2]
            and cell.bbox[0] - sign_bbox[2]
            <= pymupdf.Rect(region.bbox).width * 0.2
        ]
        if candidates:
            cell_index, cell = min(
                candidates,
                key=lambda item: item[1].bbox[0] - sign_bbox[2],
            )
            cells[cell_index] = AmountCell(
                value=f"-{cell.value}",
                bbox=cell.bbox,
                right_ratio=cell.right_ratio,
            )
            used.add(sign_index)

    label = " ".join(
        word["text"] for index, word in enumerate(words) if index not in used
    ).strip()
    line_left = min(int(word["left"]) for word in words)
    line_top = min(int(word["top"]) for word in words)
    line_right = max(int(word["left"]) + int(word["width"]) for word in words)
    line_bottom = max(int(word["top"]) + int(word["height"]) for word in words)
    raw_line = {
        "text": " ".join(word["text"] for word in words),
        "bbox": _page_bbox(
            (line_left, line_top, line_right, line_bottom),
            region,
            (image_width, image_height),
        ),
    }
    return label, cells, raw_line


def _cluster_columns(cells: list[AmountCell], tolerance: float) -> list[float]:
    clusters: list[list[float]] = []
    for position in sorted(cell.right_ratio for cell in cells):
        nearest = next(
            (
                cluster
                for cluster in clusters
                if abs(position - sum(cluster) / len(cluster)) <= tolerance
            ),
            None,
        )
        if nearest is None:
            clusters.append([position])
        else:
            nearest.append(position)
    frequent = [cluster for cluster in clusters if len(cluster) >= 2]
    selected = frequent or clusters
    selected.sort(key=lambda cluster: sum(cluster) / len(cluster))
    return [sum(cluster) / len(cluster) for cluster in selected]


def _extract_table(
    tsv: str,
    region: RegionSelection,
    config: OcrConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    width, height, lines = _tsv_lines(tsv)
    parsed: list[tuple[str, list[AmountCell]]] = []
    raw_lines: list[dict[str, Any]] = []
    all_cells: list[AmountCell] = []
    for words in lines:
        label, cells, raw = _line_amounts(
            words,
            (width, height),
            region,
        )
        raw_lines.append(raw)
        if cells:
            parsed.append((label, cells))
            all_cells.extend(cells)

    columns = _cluster_columns(all_cells, config.column_tolerance)
    records: list[dict[str, str]] = []
    coordinate_rows: list[dict[str, Any]] = []
    for label, cells in parsed:
        record = {"label": label}
        coordinate_cells: list[dict[str, Any]] = []
        for cell in cells:
            column = min(
                range(len(columns)),
                key=lambda index: abs(columns[index] - cell.right_ratio),
            )
            key = f"value_{column + 1}"
            if record.get(key):
                key = f"{key}_extra"
            record[key] = cell.value
            coordinate_cells.append(
                {"column": key, "value": cell.value, "bbox": cell.bbox}
            )
        records.append(record)
        coordinate_rows.append({"label": label, "cells": coordinate_cells})

    dataframe = pd.DataFrame(records)
    ordered = ["label", *(f"value_{i + 1}" for i in range(len(columns)))]
    extra = [column for column in dataframe.columns if column not in ordered]
    dataframe = dataframe.reindex(columns=[*ordered, *extra], fill_value="")
    details = {
        "column_right_ratios": columns,
        "rows": coordinate_rows,
        "raw_lines": raw_lines,
        "amount_count": len(all_cells),
        "multi_value_row_count": sum(len(cells) >= 2 for _, cells in parsed),
        "labeled_row_count": sum(bool(label.strip()) for label, _ in parsed),
    }
    return dataframe.fillna(""), details


def _score_tsv(tsv: str, region: RegionSelection, config: OcrConfig) -> dict[str, Any]:
    dataframe, details = _extract_table(tsv, region, config)
    text = " ".join(line["text"] for line in details["raw_lines"]).casefold()
    keyword_count = sum(keyword in text for keyword in FINANCIAL_KEYWORDS)
    multi_value_rows = sum(
        sum(bool(value) for value in row.iloc[1:]) >= 2
        for _, row in dataframe.iterrows()
    )
    single_value_rows = len(dataframe) - multi_value_rows
    fragmentation = max(0, len(details["raw_lines"]) - len(dataframe) * 3)
    score = (
        details["amount_count"] * 5
        + keyword_count * 4
        + multi_value_rows * 30
        - single_value_rows * 3
        - fragmentation
    )
    return {
        "score": score,
        "keyword_count": keyword_count,
        "dataframe": dataframe,
        "details": details,
    }


def _is_exploitable(
    scored: dict[str, Any],
    config: OcrConfig,
    region: RegionSelection,
) -> bool:
    dataframe = scored["dataframe"]
    row_count = len(dataframe)
    labeled_rows = scored["details"]["labeled_row_count"]
    multi_value_rows = scored["details"]["multi_value_row_count"]
    coherent_structure = multi_value_rows >= 3 or (
        row_count >= 8
        and dataframe.shape[1] == 2
        and labeled_rows / row_count >= 0.7
    )
    likely_narrative = (
        region.source == "rendered_page"
        and row_count < 10
        and dataframe.shape[1] > 4
    )
    return (
        scored["details"]["amount_count"] >= config.minimum_amount_count
        and scored["keyword_count"] >= 1
        and row_count >= 3
        and dataframe.shape[1] >= 2
        and labeled_rows / row_count >= 0.5
        and coherent_structure
        and not likely_narrative
    )


def _needs_layout_split(
    scored: dict[str, Any],
    region: RegionSelection,
    page: pymupdf.Page,
) -> bool:
    if region.source == "automatic_split":
        return False
    region_rect = pymupdf.Rect(region.bbox)
    if region_rect.width < page.rect.width * 0.8:
        return False
    dataframe = scored["dataframe"]
    too_many_columns = dataframe.shape[1] > 6
    concatenated_amount = any(
        len(re.sub(r"\D", "", str(value))) > 15
        for value in dataframe.iloc[:, 1:].to_numpy().ravel()
    )
    return too_many_columns or concatenated_amount


def _split_region(region: RegionSelection) -> list[RegionSelection]:
    rect = pymupdf.Rect(region.bbox)
    midpoint = rect.x0 + rect.width / 2
    return [
        RegionSelection(
            page_no=region.page_no,
            bbox=(rect.x0, rect.y0, midpoint, rect.y1),
            source="automatic_split",
            source_id=f"{region.source_id}:left",
            name=f"{region.name or region.source_id}:left",
        ),
        RegionSelection(
            page_no=region.page_no,
            bbox=(midpoint, rect.y0, rect.x1, rect.y1),
            source="automatic_split",
            source_id=f"{region.source_id}:right",
            name=f"{region.name or region.source_id}:right",
        ),
    ]


def _load_cache(
    metadata_path: Path,
    source_sha: str,
    config: OcrConfig,
    *,
    selection: dict[str, Any] | None = None,
) -> dict | None:
    if not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        metadata.get("schema_version") != OCR_SCHEMA_VERSION
        or metadata.get("source_sha256") != source_sha
        or metadata.get("config_sha256") != config.fingerprint
    ):
        return None
    if selection is not None:
        selection_sha = hashlib.sha256(
            json.dumps(selection, sort_keys=True).encode()
        ).hexdigest()
        stored_selection_sha = metadata.get("selection_sha256")
        if stored_selection_sha is not None:
            if stored_selection_sha != selection_sha:
                return None
        elif (
            metadata.get("selected_pages") != selection["selected_pages"]
            or selection["regions_json_sha256"] is not None
        ):
            return None
    output_dir = metadata_path.parent
    artifacts = metadata.get("artifact_sha256", {})
    if not artifacts:
        rejected = metadata.get("rejected_regions", [])
        valid_negative_result = (
            metadata.get("status") == "FAILED"
            and metadata.get("tables_detected") == 0
            and metadata.get("regions_examined") == len(rejected)
            and bool(rejected)
            and all(
                isinstance(region, dict)
                and isinstance(region.get("page"), int)
                and region.get("reason") in {
                    "NOT_FINANCIAL_TABLE",
                    "AUTOMATIC_LAYOUT_SPLIT",
                }
                and isinstance(region.get("attempts"), list)
                for region in rejected
            )
        )
        if not valid_negative_result:
            return None
    for relative, expected in artifacts.items():
        path = output_dir / relative
        if not path.is_file() or _sha256(path) != expected:
            return None
    if selection is not None and metadata.get("selection_sha256") is None:
        metadata["selection"] = selection
        metadata["selection_sha256"] = selection_sha
        _atomic_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
    metadata["cache_used"] = True
    return metadata


def recover_document(
    pdf_path: Path,
    *,
    parser_metadata: Path | None = None,
    pages: list[int] | None = None,
    regions_json: Path | None = None,
    output_root: Path = OUTPUT_ROOT,
    config: OcrConfig | None = None,
    force: bool = False,
) -> dict:
    config = config or OcrConfig()
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise RuntimeError("Tesseract CLI est introuvable dans le PATH.")

    source_sha = _sha256(pdf_path)
    output_dir = (
        Path(output_root)
        / f"{pdf_path.stem}_{source_sha[:12]}"
        / config.fingerprint[:12]
    )
    selected_pages = pages
    if selected_pages is None and parser_metadata is not None:
        selected_pages = _pages_from_parser_metadata(parser_metadata)
    if selected_pages is None:
        with pymupdf.open(pdf_path) as document:
            selected_pages = _automatic_pages(document)
    selection = {
        "selected_pages": selected_pages,
        "regions_json_sha256": (
            _sha256(Path(regions_json)) if regions_json is not None else None
        ),
    }
    selection_sha256 = hashlib.sha256(
        json.dumps(selection, sort_keys=True).encode()
    ).hexdigest()
    metadata_path = output_dir / "ocr_metadata.json"
    if not force:
        cached = _load_cache(
            metadata_path,
            source_sha,
            config,
            selection=selection,
        )
        if cached is not None:
            return cached

    started = time.monotonic()
    tables_dir = output_dir / "tables"
    coordinates_dir = output_dir / "coordinates"
    tables_dir.mkdir(parents=True, exist_ok=True)
    coordinates_dir.mkdir(parents=True, exist_ok=True)
    table_records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    with pymupdf.open(pdf_path) as document, tempfile.TemporaryDirectory() as temp:
        regions = (
            _regions_from_json(regions_json, document)
            if regions_json is not None
            else discover_regions(document, selected_pages, config)
        )
        temporary = Path(temp)
        for region_index, region in enumerate(regions, 1):
            image = temporary / f"region_{region_index:03d}.png"
            image_size = _render_region(
                document,
                region,
                image,
                config.render_dpi,
            )
            attempts: list[dict[str, Any]] = []
            for psm in config.psms:
                tsv = _run_tesseract(image, tesseract, config, psm)
                scored = _score_tsv(tsv, region, config)
                attempts.append({"psm": psm, **scored})
            best = max(attempts, key=lambda attempt: attempt["score"])
            attempt_summary = [
                {
                    "psm": attempt["psm"],
                    "score": attempt["score"],
                    "amount_count": attempt["details"]["amount_count"],
                    "keyword_count": attempt["keyword_count"],
                    "rows": len(attempt["dataframe"]),
                    "multi_value_rows": attempt["details"][
                        "multi_value_row_count"
                    ],
                    "labeled_rows": attempt["details"][
                        "labeled_row_count"
                    ],
                }
                for attempt in attempts
            ]
            region_payload = {
                "page": region.page_no,
                "bbox": region.bbox,
                "source": region.source,
                "source_id": region.source_id,
                "name": region.name,
                "image_size": image_size,
                "attempts": attempt_summary,
                "selected_psm": best["psm"],
            }
            page = document[region.page_no - 1]
            if _needs_layout_split(best, region, page):
                regions.extend(_split_region(region))
                rejected.append(
                    {**region_payload, "reason": "AUTOMATIC_LAYOUT_SPLIT"}
                )
                continue
            if not _is_exploitable(best, config, region):
                rejected.append({**region_payload, "reason": "NOT_FINANCIAL_TABLE"})
                continue

            table_number = len(table_records) + 1
            csv_name = f"ocr_table_{table_number:03d}.csv"
            coordinates_name = f"ocr_table_{table_number:03d}.json"
            csv_path = tables_dir / csv_name
            coordinates_path = coordinates_dir / coordinates_name
            best["dataframe"].to_csv(csv_path, index=False)
            coordinate_payload = {
                **region_payload,
                **best["details"],
            }
            _atomic_text(
                coordinates_path,
                json.dumps(coordinate_payload, ensure_ascii=False, indent=2),
            )
            table_records.append(
                {
                    "table_id": f"OCR_{table_number:03d}",
                    **region_payload,
                    "rows": len(best["dataframe"]),
                    "columns": len(best["dataframe"].columns),
                    "amount_count": best["details"]["amount_count"],
                    "csv": f"tables/{csv_name}",
                    "coordinates": f"coordinates/{coordinates_name}",
                    "quality_status": "TECHNICALLY_RECOVERED",
                    "financial_validation": "NOT_PERFORMED",
                }
            )

    artifact_paths = [
        output_dir / record[key]
        for record in table_records
        for key in ("csv", "coordinates")
    ]
    status = "PARSED" if table_records else "FAILED"
    summary = {
        "schema_version": OCR_SCHEMA_VERSION,
        "source_pdf": str(pdf_path),
        "source_sha256": source_sha,
        "parser_metadata": str(Path(parser_metadata).resolve())
        if parser_metadata
        else None,
        "config": asdict(config),
        "config_sha256": config.fingerprint,
        "selection": selection,
        "selection_sha256": selection_sha256,
        "ocr_engine": "tesseract-cli",
        "tesseract_version": _tesseract_version(tesseract),
        "selected_pages": selected_pages,
        "regions_examined": len(table_records) + len(rejected),
        "tables_detected": len(table_records),
        "tables": table_records,
        "rejected_regions": rejected,
        "duration_seconds": round(time.monotonic() - started, 3),
        "financial_validation": "NOT_PERFORMED",
        "status": status,
        "cache_used": False,
        "artifact_sha256": {
            str(path.relative_to(output_dir)): _sha256(path)
            for path in artifact_paths
        },
    }
    _atomic_text(
        metadata_path,
        json.dumps(summary, ensure_ascii=False, indent=2),
    )
    return summary


def _find_row(dataframe: pd.DataFrame, marker: str) -> pd.Series | None:
    labels = dataframe.iloc[:, 0].astype(str).str.upper()
    matches = dataframe[labels.str.contains(marker, regex=False)]
    return None if matches.empty else matches.iloc[-1]


def _validate_financial_tables(
    tables: dict[str, pd.DataFrame],
) -> tuple[str, list[dict[str, str]]]:
    checks: list[dict[str, str]] = []
    for name, dataframe in tables.items():
        passed = not dataframe.empty and dataframe.shape[1] >= 2
        checks.append(
            {
                "name": f"{name}_structure",
                "status": "PASS" if passed else "FAIL",
                "detail": str(dataframe.shape),
            }
        )

    actif = tables.get("bilan_actif_social")
    passif = tables.get("bilan_passif_social")
    if actif is not None and passif is not None:
        actif_total = _find_row(actif, "TOTAL GENERAL")
        passif_total = _find_row(passif, "TOTAL GENERAL")
        for name, offset in (("current", -2), ("prior", -1)):
            balanced = (
                actif_total is not None
                and passif_total is not None
                and _amount(str(actif_total.iloc[offset]))
                == _amount(str(passif_total.iloc[offset]))
            )
            checks.append(
                {
                    "name": f"bilan_{name}_balanced",
                    "status": "PASS" if balanced else "FAIL",
                    "detail": f"Comparaison de la colonne {offset}",
                }
            )

    status = (
        "PARSED"
        if checks and all(check["status"] == "PASS" for check in checks)
        else "PARTIAL"
    )
    return status, checks


def main() -> None:
    cli = argparse.ArgumentParser(description="OCR financier générique AMMC")
    cli.add_argument("pdf", type=Path)
    cli.add_argument("--parser-metadata", type=Path)
    cli.add_argument("--pages", type=_parse_pages)
    cli.add_argument("--regions-json", type=Path)
    cli.add_argument("--force", action="store_true")
    args = cli.parse_args()
    summary = recover_document(
        args.pdf,
        parser_metadata=args.parser_metadata,
        pages=args.pages,
        regions_json=args.regions_json,
        force=args.force,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
