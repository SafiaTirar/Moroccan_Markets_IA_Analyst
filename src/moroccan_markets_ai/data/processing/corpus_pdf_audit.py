"""Audit technique léger du corpus annuel AMMC avec PyMuPDF uniquement."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MANIFEST_FILE = PROJECT_ROOT / "data" / "raw" / "ammc" / "corpus" / "download_manifest.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "ammc" / "corpus_audit"
AUDIT_FILE = OUTPUT_DIR / "annual_pdf_audit.csv"
PAGES_FILE = OUTPUT_DIR / "annual_pdf_pages.csv"
PLAN_FILE = OUTPUT_DIR / "annual_processing_plan.csv"
REPORT_FILE = OUTPUT_DIR / "annual_pdf_audit_report.json"
AUDIT_SCHEMA_VERSION = "1.2"

REQUIRED_MANIFEST_COLUMNS = {
    "company_id",
    "issuer",
    "exercise_year",
    "report_type",
    "report_family",
    "perimeter",
    "detail_url",
    "pdf_url",
    "local_path",
    "sha256",
    "size_bytes",
}

FINANCIAL_KEYWORDS = {
    "etats financiers": "états financiers",
    "bilan": "bilan",
    "actif": "actif",
    "passif": "passif",
    "compte de produits et charges": "compte de produits et charges",
    "resultat net": "résultat net",
    "chiffre d'affaires": "chiffre d'affaires",
    "capitaux propres": "capitaux propres",
    "flux de tresorerie": "flux de trésorerie",
    "tableau des flux": "tableau des flux",
    "produit net bancaire": "produit net bancaire",
}

PROVENANCE_COLUMNS = [
    "company_id",
    "issuer",
    "exercise_year",
    "report_type",
    "perimeter",
    "local_path",
    "sha256",
    "pdf_url",
    "detail_url",
]

AUDIT_COLUMNS = [
    *PROVENANCE_COLUMNS,
    "manifest_size_bytes",
    "actual_size_bytes",
    "size_matches",
    "actual_sha256",
    "sha256_matches",
    "manifest_valid",
    "manifest_issues",
    "audit_status",
    "error",
    "page_count",
    "text_page_count",
    "no_text_page_count",
    "total_text_characters",
    "mean_text_characters_per_page",
    "median_text_characters_per_page",
    "image_count",
    "image_area_points2",
    "mean_image_area_ratio",
    "text_class_pages",
    "image_class_pages",
    "mixed_class_pages",
    "unknown_class_pages",
    "ocr_candidate_count",
    "ocr_candidate_pages",
    "financial_candidate_count",
    "financial_candidate_pages",
    "document_classification",
    "audit_config_sha256",
    "cache_status",
]

PAGE_COLUMNS = [
    *PROVENANCE_COLUMNS,
    "page_number",
    "width_points",
    "height_points",
    "page_area_points2",
    "text_characters",
    "word_count",
    "has_extractable_text",
    "image_count",
    "unique_image_count",
    "image_area_points2",
    "image_area_ratio",
    "page_classification",
    "ocr_candidate",
    "financial_keyword_count",
    "financial_keywords",
    "page_error",
    "audit_config_sha256",
]

PLAN_COLUMNS = [
    *PROVENANCE_COLUMNS,
    "audit_status",
    "manifest_valid",
    "document_classification",
    "recommended_strategy",
    "ocr_candidate_count",
    "ocr_candidate_pages",
    "financial_candidate_pages",
    "estimated_workload_units",
    "priority",
    "processing_order",
    "batch_id",
    "max_parallel_documents",
    "decision_reason",
]


@dataclass(frozen=True)
class AuditConfig:
    """Seuils déterministes; les surfaces sont rapportées à la page."""

    min_text_characters: int = 40
    substantial_text_characters: int = 200
    min_image_area_ratio: float = 0.15
    text_document_min_ratio: float = 0.80
    image_document_min_ratio: float = 0.80
    max_text_document_ocr_ratio: float = 0.20
    batch_max_workload_units: int = 400
    large_document_pages: int = 250
    large_document_size_mb: int = 25

    def signature(self) -> str:
        payload = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "config": asdict(self),
            "financial_keywords": sorted(FINANCIAL_KEYWORDS),
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode()).hexdigest()


class ManifestError(ValueError):
    """Le manifeste ne peut pas être interprété de façon fiable."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join(normalized.encode("ascii", "ignore").decode().lower().split())


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def atomic_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def manifest_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ManifestError(f"Manifeste absent : {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_MANIFEST_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ManifestError(
                f"Colonnes obligatoires absentes : {', '.join(sorted(missing))}"
            )
        return list(reader)


def resolve_local_path(local_path: str, project_root: Path) -> tuple[Path, list[str]]:
    candidate = Path(local_path)
    resolved = (candidate if candidate.is_absolute() else project_root / candidate).resolve()
    issues = []
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError:
        issues.append("LOCAL_PATH_OUTSIDE_PROJECT")
    return resolved, issues


def check_manifest_entry(
    row: dict[str, str], project_root: Path
) -> tuple[Path, dict[str, Any]]:
    path, issues = resolve_local_path(row.get("local_path", ""), project_root)
    if not row.get("company_id", "").strip():
        issues.append("MISSING_COMPANY_ID")
    if not row.get("issuer", "").strip():
        issues.append("MISSING_ISSUER")
    try:
        year = int(row.get("exercise_year", ""))
        if year < 2000 or year > 2100:
            issues.append("INVALID_EXERCISE_YEAR")
    except ValueError:
        issues.append("INVALID_EXERCISE_YEAR")
    if row.get("report_family") != "ANNUAL":
        issues.append("NOT_ANNUAL_REPORT")
    if "annuel" not in normalize_text(row.get("report_type", "")):
        issues.append("REPORT_TYPE_NOT_ANNUAL")
    for field in ("pdf_url", "detail_url"):
        parsed = urlparse(row.get(field, ""))
        if parsed.scheme != "https" or parsed.hostname not in {"ammc.ma", "www.ammc.ma"}:
            issues.append(f"INVALID_{field.upper()}")

    actual_size: int | None = None
    actual_sha = ""
    size_matches = False
    sha_matches = False
    if not path.is_file():
        issues.append("FILE_MISSING")
    else:
        actual_size = path.stat().st_size
        try:
            declared_size = int(row.get("size_bytes", ""))
            size_matches = declared_size == actual_size
        except ValueError:
            declared_size = None
        if declared_size is None:
            issues.append("INVALID_MANIFEST_SIZE")
        elif not size_matches:
            issues.append("SIZE_MISMATCH")
        actual_sha = sha256_file(path)
        declared_sha = row.get("sha256", "").lower()
        sha_matches = len(declared_sha) == 64 and declared_sha == actual_sha
        if not sha_matches:
            issues.append("SHA256_MISMATCH")
    return path, {
        "actual_size_bytes": actual_size if actual_size is not None else "",
        "actual_sha256": actual_sha,
        "size_matches": size_matches,
        "sha256_matches": sha_matches,
        "manifest_valid": not issues,
        "manifest_issues": ";".join(issues),
    }


def page_classification(
    text_characters: int, image_area_ratio: float, config: AuditConfig
) -> str:
    has_text = text_characters >= config.min_text_characters
    has_image = image_area_ratio >= config.min_image_area_ratio
    if has_text and has_image:
        return "MIXED"
    if has_text:
        return "TEXT"
    if has_image:
        return "IMAGE"
    return "UNKNOWN"


def is_ocr_candidate(
    classification: str,
    text_characters: int,
    image_count: int,
    config: AuditConfig,
) -> bool:
    if classification == "IMAGE":
        return True
    if classification == "UNKNOWN" and text_characters == 0 and image_count > 0:
        return True
    return (
        classification == "MIXED"
        and text_characters < config.substantial_text_characters
    )


def image_metrics(page: pymupdf.Page) -> tuple[int, int, float, float]:
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    image_area = 0.0
    image_xrefs: set[int] = set()
    images = page.get_image_info(xrefs=True)
    for image in images:
        bbox = pymupdf.Rect(image.get("bbox", (0, 0, 0, 0))) & page.rect
        if not bbox.is_empty and not bbox.is_infinite:
            image_area += max(float(bbox.width * bbox.height), 0.0)
        xref = int(image.get("xref", 0) or 0)
        if xref:
            image_xrefs.add(xref)
    return len(images), len(image_xrefs), image_area, min(image_area / page_area, 1.0)


def provenance(row: dict[str, str]) -> dict[str, str]:
    return {column: row.get(column, "") for column in PROVENANCE_COLUMNS}


def document_classification(page_rows: list[dict[str, Any]], config: AuditConfig) -> str:
    if not page_rows:
        return "UNKNOWN"
    counts = Counter(row["page_classification"] for row in page_rows)
    page_count = len(page_rows)
    text_bearing = counts["TEXT"] + counts["MIXED"]
    ocr_count = sum(bool(row["ocr_candidate"]) for row in page_rows)
    if counts["IMAGE"] / page_count >= config.image_document_min_ratio:
        return "IMAGE"
    if (
        text_bearing / page_count >= config.text_document_min_ratio
        and ocr_count / page_count <= config.max_text_document_ocr_ratio
    ):
        return "TEXT"
    if text_bearing or counts["IMAGE"]:
        return "MIXED"
    return "UNKNOWN"


def audit_pdf(
    pdf_path: Path,
    manifest_row: dict[str, str],
    *,
    config: AuditConfig,
    config_sha: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    page_rows: list[dict[str, Any]] = []
    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 - frontière d'isolation par PDF
        return {
            "audit_status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "page_count": 0,
            "document_classification": "UNKNOWN",
        }, page_rows

    try:
        for page_number in range(document.page_count):
            page_data: dict[str, Any] = {
                **provenance(manifest_row),
                "page_number": page_number + 1,
                "audit_config_sha256": config_sha,
                "page_error": "",
            }
            try:
                page = document.load_page(page_number)
                page_area = float(page.rect.width * page.rect.height)
                text = page.get_text("text").strip()
                text_characters = len(text)
                word_count = len(page.get_text("words"))
                image_count, unique_images, image_area, image_ratio = image_metrics(page)
                classification = page_classification(
                    text_characters, image_ratio, config
                )
                normalized = normalize_text(text)
                keywords = [
                    label
                    for token, label in FINANCIAL_KEYWORDS.items()
                    if token in normalized
                ]
                page_data.update(
                    {
                        "width_points": round(float(page.rect.width), 3),
                        "height_points": round(float(page.rect.height), 3),
                        "page_area_points2": round(page_area, 3),
                        "text_characters": text_characters,
                        "word_count": word_count,
                        "has_extractable_text": bool(text_characters),
                        "image_count": image_count,
                        "unique_image_count": unique_images,
                        "image_area_points2": round(image_area, 3),
                        "image_area_ratio": round(image_ratio, 6),
                        "page_classification": classification,
                        "ocr_candidate": is_ocr_candidate(
                            classification, text_characters, image_count, config
                        ),
                        "financial_keyword_count": len(keywords),
                        "financial_keywords": ";".join(keywords),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - poursuite après une page fautive
                page_data.update(
                    {
                        "width_points": "",
                        "height_points": "",
                        "page_area_points2": "",
                        "text_characters": 0,
                        "word_count": 0,
                        "has_extractable_text": False,
                        "image_count": 0,
                        "unique_image_count": 0,
                        "image_area_points2": 0,
                        "image_area_ratio": 0,
                        "page_classification": "UNKNOWN",
                        "ocr_candidate": True,
                        "financial_keyword_count": 0,
                        "financial_keywords": "",
                        "page_error": f"{type(exc).__name__}: {exc}",
                    }
                )
            page_rows.append(page_data)
    finally:
        document.close()

    text_counts = [int(row["text_characters"]) for row in page_rows]
    image_areas = [float(row["image_area_points2"]) for row in page_rows]
    image_ratios = [float(row["image_area_ratio"]) for row in page_rows]
    class_counts = Counter(row["page_classification"] for row in page_rows)
    ocr_pages = [int(row["page_number"]) for row in page_rows if row["ocr_candidate"]]
    financial_pages = [
        int(row["page_number"])
        for row in page_rows
        if int(row["financial_keyword_count"]) > 0
    ]
    page_errors = [row for row in page_rows if row["page_error"]]
    summary = {
        "audit_status": "PARTIAL" if page_errors else "SUCCESS",
        "error": f"{len(page_errors)} page(s) illisible(s)" if page_errors else "",
        "page_count": len(page_rows),
        "text_page_count": sum(bool(row["has_extractable_text"]) for row in page_rows),
        "no_text_page_count": sum(not bool(row["has_extractable_text"]) for row in page_rows),
        "total_text_characters": sum(text_counts),
        "mean_text_characters_per_page": round(statistics.fmean(text_counts), 2)
        if text_counts
        else 0,
        "median_text_characters_per_page": round(statistics.median(text_counts), 2)
        if text_counts
        else 0,
        "image_count": sum(int(row["image_count"]) for row in page_rows),
        "image_area_points2": round(sum(image_areas), 3),
        "mean_image_area_ratio": round(statistics.fmean(image_ratios), 6)
        if image_ratios
        else 0,
        "text_class_pages": class_counts["TEXT"],
        "image_class_pages": class_counts["IMAGE"],
        "mixed_class_pages": class_counts["MIXED"],
        "unknown_class_pages": class_counts["UNKNOWN"],
        "ocr_candidate_count": len(ocr_pages),
        "ocr_candidate_pages": ";".join(map(str, ocr_pages)),
        "financial_candidate_count": len(financial_pages),
        "financial_candidate_pages": ";".join(map(str, financial_pages)),
        "document_classification": document_classification(page_rows, config),
    }
    return summary, page_rows


def empty_audit_summary() -> dict[str, Any]:
    return {
        "text_page_count": 0,
        "no_text_page_count": 0,
        "total_text_characters": 0,
        "mean_text_characters_per_page": 0,
        "median_text_characters_per_page": 0,
        "image_count": 0,
        "image_area_points2": 0,
        "mean_image_area_ratio": 0,
        "text_class_pages": 0,
        "image_class_pages": 0,
        "mixed_class_pages": 0,
        "unknown_class_pages": 0,
        "ocr_candidate_count": 0,
        "ocr_candidate_pages": "",
        "financial_candidate_count": 0,
        "financial_candidate_pages": "",
    }


def cache_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (row.get("company_id", ""), row.get("exercise_year", ""), row.get("local_path", ""))


def reusable_cache(
    audit_path: Path, pages_path: Path, config_sha: str
) -> tuple[dict[tuple[str, str, str], dict[str, str]], dict[tuple[str, str, str], list[dict[str, str]]]]:
    audits = {}
    for row in read_csv(audit_path):
        if row.get("audit_status") == "SUCCESS" and row.get("audit_config_sha256") == config_sha:
            audits[cache_key(row)] = row
    pages: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in read_csv(pages_path):
        if row.get("audit_config_sha256") == config_sha:
            pages.setdefault(cache_key(row), []).append(row)
    return audits, pages


def numeric(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_processing_plan(
    audits: list[dict[str, Any]], config: AuditConfig
) -> list[dict[str, Any]]:
    plans = []
    for audit in audits:
        classification = audit["document_classification"]
        manifest_valid = str(audit["manifest_valid"]).lower() == "true"
        if audit["audit_status"] != "SUCCESS" or not manifest_valid:
            strategy, priority = "MANUAL_REVIEW", 4
            reason = "audit ou provenance invalide; traitement automatique bloqué"
        elif classification == "TEXT":
            strategy, priority = "DOCLING", 1
            reason = "couverture textuelle suffisante; Docling prioritaire"
        elif classification == "MIXED":
            strategy, priority = "HYBRID", 2
            reason = "Docling puis OCR limité aux pages candidates"
        elif classification == "IMAGE":
            strategy, priority = "OCR", 3
            reason = "document majoritairement image; OCR requis avant extraction"
        else:
            strategy, priority = "MANUAL_REVIEW", 4
            reason = "contenu insuffisant ou inconnu"
        pages = int(numeric(audit.get("page_count")))
        ocr_pages = int(numeric(audit.get("ocr_candidate_count")))
        workload = pages + 4 * ocr_pages
        plans.append(
            {
                **{column: audit.get(column, "") for column in PROVENANCE_COLUMNS},
                "audit_status": audit["audit_status"],
                "manifest_valid": audit["manifest_valid"],
                "document_classification": classification,
                "recommended_strategy": strategy,
                "ocr_candidate_count": ocr_pages,
                "ocr_candidate_pages": audit.get("ocr_candidate_pages", ""),
                "financial_candidate_pages": audit.get("financial_candidate_pages", ""),
                "estimated_workload_units": workload,
                "priority": priority,
                "decision_reason": reason,
                "max_parallel_documents": 1,
            }
        )
    plans.sort(
        key=lambda row: (
            int(row["priority"]),
            int(row["estimated_workload_units"]),
            row["company_id"],
            row["exercise_year"],
        )
    )
    batch_id = 1
    batch_load = 0
    for order, plan in enumerate(plans, start=1):
        load = int(plan["estimated_workload_units"])
        if batch_load and batch_load + load > config.batch_max_workload_units:
            batch_id += 1
            batch_load = 0
        plan["processing_order"] = order
        plan["batch_id"] = batch_id
        batch_load += load
    return plans


def distribution(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"min": None, "median": None, "mean": None, "max": None, "total": 0}
    return {
        "min": min(values),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.fmean(values), 2),
        "max": max(values),
        "total": sum(values),
    }


def build_report(
    audits: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    *,
    config: AuditConfig,
    config_sha: str,
    duplicate_keys: list[tuple[str, str]],
) -> dict[str, Any]:
    statuses = Counter(row["audit_status"] for row in audits)
    classes = Counter(row["document_classification"] for row in audits)
    batch_loads: Counter[int] = Counter()
    for plan in plans:
        batch_loads[int(plan["batch_id"])] += int(plan["estimated_workload_units"])
    sizes = [int(numeric(row["actual_size_bytes"])) for row in audits if numeric(row["actual_size_bytes"]) > 0]
    pages = [int(numeric(row["page_count"])) for row in audits if numeric(row["page_count"]) > 0]
    unknown_perimeters = sum(
        normalize_text(str(row.get("perimeter", ""))) in {"", "unknown"}
        for row in audits
    )
    difficult = []
    for audit, plan in zip(audits, [next(item for item in plans if cache_key(item) == cache_key(audit)) for audit in audits], strict=True):
        reasons = []
        page_count = int(numeric(audit["page_count"]))
        size_bytes = int(numeric(audit["actual_size_bytes"]))
        ocr_count = int(numeric(audit["ocr_candidate_count"]))
        if audit["audit_status"] != "SUCCESS":
            reasons.append(audit["audit_status"])
        if str(audit["manifest_valid"]).lower() != "true":
            reasons.append("INVALID_PROVENANCE")
        if page_count >= config.large_document_pages:
            reasons.append("LARGE_PAGE_COUNT")
        if size_bytes >= config.large_document_size_mb * 1024 * 1024:
            reasons.append("LARGE_FILE_SIZE")
        if page_count and ocr_count / page_count >= 0.25:
            reasons.append("HIGH_OCR_PAGE_RATIO")
        if plan["recommended_strategy"] in {"OCR", "MANUAL_REVIEW"}:
            reasons.append(f"STRATEGY_{plan['recommended_strategy']}")
        if reasons:
            difficult.append(
                {
                    "company_id": audit["company_id"],
                    "issuer": audit["issuer"],
                    "exercise_year": audit["exercise_year"],
                    "local_path": audit["local_path"],
                    "reasons": sorted(set(reasons)),
                    "pages": page_count,
                    "size_bytes": size_bytes,
                    "ocr_candidate_pages": ocr_count,
                }
            )
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "engine": {"name": "PyMuPDF", "version": pymupdf.__version__},
        "docling_used": False,
        "ocr_used": False,
        "configuration": asdict(config),
        "audit_config_sha256": config_sha,
        "coverage": {
            "manifest_rows": len(audits),
            "successful": statuses["SUCCESS"],
            "partial": statuses["PARTIAL"],
            "failed": statuses["FAILED"],
            "manifest_valid": sum(str(row["manifest_valid"]).lower() == "true" for row in audits),
            "manifest_invalid": sum(str(row["manifest_valid"]).lower() != "true" for row in audits),
            "cached_documents": sum(row.get("cache_status") == "CACHED" for row in audits),
            "duplicate_company_year_keys": [list(key) for key in duplicate_keys],
        },
        "documents_by_classification": dict(sorted(classes.items())),
        "pages_by_classification": {
            "TEXT": sum(int(numeric(row["text_class_pages"])) for row in audits),
            "IMAGE": sum(int(numeric(row["image_class_pages"])) for row in audits),
            "MIXED": sum(int(numeric(row["mixed_class_pages"])) for row in audits),
            "UNKNOWN": sum(int(numeric(row["unknown_class_pages"])) for row in audits),
        },
        "total_pages": sum(int(numeric(row["page_count"])) for row in audits),
        "total_text_pages": sum(int(numeric(row["text_page_count"])) for row in audits),
        "total_no_text_pages": sum(int(numeric(row["no_text_page_count"])) for row in audits),
        "total_text_characters": sum(
            int(numeric(row["total_text_characters"])) for row in audits
        ),
        "total_image_placements": sum(
            int(numeric(row["image_count"])) for row in audits
        ),
        "total_image_area_points2": round(
            sum(numeric(row["image_area_points2"]) for row in audits), 3
        ),
        "total_ocr_candidate_pages": sum(int(numeric(row["ocr_candidate_count"])) for row in audits),
        "total_financial_candidate_pages": sum(int(numeric(row["financial_candidate_count"])) for row in audits),
        "size_bytes_distribution": distribution(sizes),
        "page_count_distribution": distribution(pages),
        "processing_strategies": dict(sorted(Counter(row["recommended_strategy"] for row in plans).items())),
        "batch_count": max((int(row["batch_id"]) for row in plans), default=0),
        "batch_policy": {
            "max_parallel_documents": 1,
            "target_workload_units_per_batch": config.batch_max_workload_units,
            "actual_max_batch_workload_units": max(batch_loads.values(), default=0),
            "oversized_documents_get_dedicated_batch": True,
            "workload_formula": "page_count + 4 * ocr_candidate_count",
            "units_are_relative_not_elapsed_time": True,
        },
        "technically_difficult_documents": difficult,
        "metadata_limitations": ([
            {
                "code": "UNKNOWN_PERIMETER",
                "document_count": unknown_perimeters,
                "impact": (
                    "Le périmètre social ou consolidé doit être établi avant "
                    "toute comparaison financière."
                ),
            }
        ] if unknown_perimeters else []),
        "anomalies": [
            {
                "company_id": row["company_id"],
                "exercise_year": row["exercise_year"],
                "local_path": row["local_path"],
                "manifest_issues": row["manifest_issues"],
                "audit_status": row["audit_status"],
                "error": row["error"],
            }
            for row in audits
            if row["manifest_issues"] or row["audit_status"] != "SUCCESS"
        ],
    }


def run_audit(
    *,
    manifest_path: Path = MANIFEST_FILE,
    output_dir: Path = OUTPUT_DIR,
    project_root: Path = PROJECT_ROOT,
    config: AuditConfig | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    active_config = config or AuditConfig()
    config_sha = active_config.signature()
    rows = manifest_rows(manifest_path)
    keys = [(row.get("company_id", ""), row.get("exercise_year", "")) for row in rows]
    duplicate_keys = sorted(key for key, count in Counter(keys).items() if count > 1)
    audit_path = output_dir / AUDIT_FILE.name
    pages_path = output_dir / PAGES_FILE.name
    cached_audits, cached_pages = (
        reusable_cache(audit_path, pages_path, config_sha) if resume else ({}, {})
    )

    audits: list[dict[str, Any]] = []
    all_pages: list[dict[str, Any]] = []
    for row in rows:
        path, integrity = check_manifest_entry(row, project_root)
        key = cache_key(row)
        cached = cached_audits.get(key)
        cache_valid = (
            cached is not None
            and integrity["manifest_valid"]
            and cached.get("actual_sha256") == integrity["actual_sha256"]
            and int(numeric(cached.get("actual_size_bytes"))) == integrity["actual_size_bytes"]
            and key in cached_pages
            and len(cached_pages[key]) == int(numeric(cached.get("page_count")))
        )
        if cache_valid:
            audit = dict(cached)
            audit["cache_status"] = "CACHED"
            page_rows: list[dict[str, Any]] = list(cached_pages[key])
        elif not path.is_file():
            audit = {
                **provenance(row),
                "manifest_size_bytes": row.get("size_bytes", ""),
                **integrity,
                **empty_audit_summary(),
                "audit_status": "FAILED",
                "error": "FileNotFoundError: fichier PDF absent",
                "page_count": 0,
                "document_classification": "UNKNOWN",
                "audit_config_sha256": config_sha,
                "cache_status": "AUDITED",
            }
            page_rows = []
        else:
            summary, page_rows = audit_pdf(
                path, row, config=active_config, config_sha=config_sha
            )
            audit = {
                **provenance(row),
                "manifest_size_bytes": row.get("size_bytes", ""),
                **integrity,
                **empty_audit_summary(),
                **summary,
                "audit_config_sha256": config_sha,
                "cache_status": "AUDITED",
            }
        audits.append(audit)
        all_pages.extend(page_rows)

    plans = build_processing_plan(audits, active_config)
    report = build_report(
        audits,
        plans,
        config=active_config,
        config_sha=config_sha,
        duplicate_keys=duplicate_keys,
    )
    atomic_csv(audit_path, audits, AUDIT_COLUMNS)
    atomic_csv(pages_path, all_pages, PAGE_COLUMNS)
    atomic_csv(output_dir / PLAN_FILE.name, plans, PLAN_COLUMNS)
    atomic_json(output_dir / REPORT_FILE.name, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_FILE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    report = run_audit(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        resume=not args.no_resume,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
