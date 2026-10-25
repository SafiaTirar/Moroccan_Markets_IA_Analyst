from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import pandas as pd
from pandas.errors import EmptyDataError, ParserError
from pydantic import BaseModel, Field

VALIDATION_SCHEMA_VERSION = "2.0"

FINANCIAL_PAGE_KEYWORDS = (
    "bilan",
    "capitaux propres",
    "chiffre d'affaires",
    "chiffre d’affaires",
    "compte de produits et charges",
    "comptes sociaux",
    "produits d'exploitation",
    "résultat net",
)
LARGE_PICTURE_PAGE_RATIO = 0.25


class ParserStatus(StrEnum):
    PARSED = "PARSED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: Literal["WARNING", "ERROR"]
    table_index: int | None = None
    pages: list[int] = Field(default_factory=list)


class TableValidationResult(BaseModel):
    table_index: int
    pages: list[int] = Field(default_factory=list)
    row_count: int
    column_count: int
    non_empty_cell_count: int
    csv_path: str
    status: Literal["VALID", "INCOMPLETE", "INVALID"]
    issues: list[ValidationIssue] = Field(default_factory=list)


class ParserValidationReport(BaseModel):
    schema_version: str = VALIDATION_SCHEMA_VERSION
    status: ParserStatus
    valid_table_count: int
    incomplete_table_count: int
    invalid_table_count: int
    issues: list[ValidationIssue] = Field(default_factory=list)
    tables: list[TableValidationResult] = Field(default_factory=list)
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    ocr_required_pages: list[int] = Field(default_factory=list)
    ocr_required_table_indexes: list[int] = Field(default_factory=list)


@dataclass(frozen=True)
class TableCandidate:
    table_index: int
    pages: list[int]
    dataframe: pd.DataFrame
    csv_path: Path


def calculate_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _issue(
    code: str,
    message: str,
    severity: Literal["WARNING", "ERROR"],
    *,
    table_index: int | None = None,
    pages: list[int] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        severity=severity,
        table_index=table_index,
        pages=pages or [],
    )


def _non_empty_cell_count(dataframe: pd.DataFrame) -> int:
    if dataframe.empty:
        return 0

    normalized = dataframe.fillna("").astype(str)
    return int(normalized.map(str.strip).ne("").sum().sum())


def _has_generated_headers(dataframe: pd.DataFrame) -> bool:
    headers = [str(column).strip() for column in dataframe.columns]
    return bool(headers) and headers == [
        str(index) for index in range(len(headers))
    ]


def _has_missing_row_labels(dataframe: pd.DataFrame) -> bool:
    if dataframe.shape[1] < 2 or dataframe.empty:
        return False

    normalized = dataframe.fillna("").astype(str).map(str.strip)
    return bool(
        (
            normalized.iloc[:, 0].eq("")
            & normalized.iloc[:, 1:].ne("").any(axis=1)
        ).any()
    )


def _has_split_words(dataframe: pd.DataFrame) -> bool:
    values = [str(column) for column in dataframe.columns]
    values.extend(
        dataframe.fillna("").astype(str).to_numpy().ravel().tolist()
    )
    return any(re.search(r"\w-\s+\w", value) for value in values)


class ParserValidator:
    def validate(
        self,
        *,
        output_dir: Path,
        page_count: int,
        tables: list[TableCandidate],
    ) -> ParserValidationReport:
        output_dir = Path(output_dir)
        issues: list[ValidationIssue] = []
        table_results = [
            self._validate_table(candidate, page_count)
            for candidate in tables
        ]

        for result in table_results:
            issues.extend(result.issues)

        issues.extend(
            self._validate_document_artifacts(
                output_dir=output_dir,
                page_count=page_count,
                tables=tables,
            )
        )

        if not tables:
            issues.append(
                _issue(
                    "NO_TABLES",
                    "Aucun tableau n'a été détecté dans le document.",
                    "ERROR",
                )
            )

        valid_count = sum(
            result.status == "VALID" for result in table_results
        )
        incomplete_count = sum(
            result.status == "INCOMPLETE" for result in table_results
        )
        invalid_count = sum(
            result.status == "INVALID" for result in table_results
        )

        document_failure_codes = {
            "DOCUMENT_JSON_INVALID",
            "DOCUMENT_JSON_MISSING",
            "DOCUMENT_MARKDOWN_MISSING",
            "DOCUMENT_PAGE_COUNT_MISMATCH",
            "NO_PAGES",
            "NO_TABLES",
        }

        if (
            page_count < 1
            or valid_count + incomplete_count == 0
            or any(
                issue.code in document_failure_codes
                for issue in issues
            )
        ):
            status = ParserStatus.FAILED
        elif issues:
            status = ParserStatus.PARTIAL
        else:
            status = ParserStatus.PARSED

        hashes = self._artifact_hashes(output_dir, tables)
        ocr_issue_codes = {
            "EMPTY_TABLE",
            "FINANCIAL_IMAGE_REQUIRES_OCR",
            "IMAGE_TABLE_REQUIRES_OCR",
        }
        ocr_issues = [
            issue for issue in issues if issue.code in ocr_issue_codes
        ]

        return ParserValidationReport(
            status=status,
            valid_table_count=valid_count,
            incomplete_table_count=incomplete_count,
            invalid_table_count=invalid_count,
            issues=issues,
            tables=table_results,
            artifact_sha256=hashes,
            ocr_required_pages=sorted(
                {page for issue in ocr_issues for page in issue.pages}
            ),
            ocr_required_table_indexes=sorted(
                {
                    issue.table_index
                    for issue in ocr_issues
                    if issue.table_index is not None
                }
            ),
        )

    def _validate_table(
        self,
        candidate: TableCandidate,
        page_count: int,
    ) -> TableValidationResult:
        dataframe = candidate.dataframe
        row_count, column_count = dataframe.shape
        non_empty_count = _non_empty_cell_count(dataframe)
        issues: list[ValidationIssue] = []
        context = {
            "table_index": candidate.table_index,
            "pages": candidate.pages,
        }

        if row_count < 1 or column_count < 1 or non_empty_count < 1:
            issues.append(
                _issue(
                    "EMPTY_TABLE",
                    "Le tableau ne contient aucune cellule exploitable.",
                    "ERROR",
                    **context,
                )
            )
            if candidate.pages:
                issues.append(
                    _issue(
                        "IMAGE_TABLE_REQUIRES_OCR",
                        "Le tableau vide doit être revu avec OCR.",
                        "WARNING",
                        **context,
                    )
                )

        if not candidate.pages:
            issues.append(
                _issue(
                    "MISSING_PAGE_PROVENANCE",
                    "La page source du tableau est inconnue.",
                    "WARNING",
                    **context,
                )
            )
        elif any(
            page < 1 or page > page_count
            for page in candidate.pages
        ):
            issues.append(
                _issue(
                    "INVALID_PAGE_PROVENANCE",
                    "Une page source est hors des limites du document.",
                    "ERROR",
                    **context,
                )
            )

        if _has_generated_headers(dataframe):
            issues.append(
                _issue(
                    "AUTOGENERATED_HEADER",
                    "Les en-têtes de colonnes sont artificiels.",
                    "WARNING",
                    **context,
                )
            )

        if _has_missing_row_labels(dataframe):
            issues.append(
                _issue(
                    "MISSING_ROW_LABEL",
                    "Au moins une ligne chiffrée n'a pas de libellé.",
                    "WARNING",
                    **context,
                )
            )

        if _has_split_words(dataframe):
            issues.append(
                _issue(
                    "SPLIT_WORD",
                    "Un libellé contient une césure issue de la mise en page.",
                    "WARNING",
                    **context,
                )
            )

        issues.extend(self._validate_csv(candidate))

        if any(issue.severity == "ERROR" for issue in issues):
            status = "INVALID"
        elif issues:
            status = "INCOMPLETE"
        else:
            status = "VALID"

        return TableValidationResult(
            table_index=candidate.table_index,
            pages=candidate.pages,
            row_count=row_count,
            column_count=column_count,
            non_empty_cell_count=non_empty_count,
            csv_path=str(candidate.csv_path),
            status=status,
            issues=issues,
        )

    def _validate_csv(
        self,
        candidate: TableCandidate,
    ) -> list[ValidationIssue]:
        path = candidate.csv_path
        context = {
            "table_index": candidate.table_index,
            "pages": candidate.pages,
        }

        if not path.is_file():
            return [
                _issue(
                    "MISSING_CSV",
                    f"CSV attendu absent : {path.name}",
                    "ERROR",
                    **context,
                )
            ]

        try:
            csv_dataframe = pd.read_csv(
                path,
                dtype=str,
                keep_default_na=False,
            )
        except (EmptyDataError, ParserError, OSError, UnicodeError) as exc:
            return [
                _issue(
                    "CSV_UNREADABLE",
                    f"CSV illisible ({path.name}) : {exc}",
                    "ERROR",
                    **context,
                )
            ]

        if csv_dataframe.shape != candidate.dataframe.shape:
            return [
                _issue(
                    "CSV_SHAPE_MISMATCH",
                    (
                        f"Dimensions CSV {csv_dataframe.shape} différentes "
                        f"des dimensions Docling {candidate.dataframe.shape}."
                    ),
                    "ERROR",
                    **context,
                )
            ]

        return []

    def _validate_document_artifacts(
        self,
        *,
        output_dir: Path,
        page_count: int,
        tables: list[TableCandidate],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        markdown_path = output_dir / "document.md"
        json_path = output_dir / "document.json"

        if page_count < 1:
            issues.append(
                _issue(
                    "NO_PAGES",
                    "Le document converti ne contient aucune page.",
                    "ERROR",
                )
            )

        if not markdown_path.is_file() or markdown_path.stat().st_size == 0:
            issues.append(
                _issue(
                    "DOCUMENT_MARKDOWN_MISSING",
                    "document.md est absent ou vide.",
                    "ERROR",
                )
            )

        document_data: dict | None = None
        if not json_path.is_file() or json_path.stat().st_size == 0:
            issues.append(
                _issue(
                    "DOCUMENT_JSON_MISSING",
                    "document.json est absent ou vide.",
                    "ERROR",
                )
            )
        else:
            try:
                loaded = json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise TypeError("la racine JSON n'est pas un objet")
                document_data = loaded
            except (OSError, TypeError, UnicodeError, ValueError) as exc:
                issues.append(
                    _issue(
                        "DOCUMENT_JSON_INVALID",
                        f"document.json est invalide : {exc}",
                        "ERROR",
                    )
                )

        if document_data is not None:
            json_page_count = len(document_data.get("pages", {}))
            if json_page_count != page_count:
                issues.append(
                    _issue(
                        "DOCUMENT_PAGE_COUNT_MISMATCH",
                        (
                            f"Le JSON contient {json_page_count} pages, "
                            f"contre {page_count} attendues."
                        ),
                        "ERROR",
                    )
                )

            json_table_count = len(document_data.get("tables", []))
            if json_table_count != len(tables):
                issues.append(
                    _issue(
                        "DOCUMENT_TABLE_COUNT_MISMATCH",
                        (
                            f"Le JSON contient {json_table_count} tables, "
                            f"contre {len(tables)} attendues."
                        ),
                        "ERROR",
                    )
                )

            issues.extend(
                self._detect_financial_images_without_tables(
                    document_data=document_data,
                    tables=tables,
                )
            )

        tables_dir = output_dir / "tables"
        expected_names = {
            candidate.csv_path.name for candidate in tables
        }
        actual_names = (
            {path.name for path in tables_dir.glob("table_*.csv")}
            if tables_dir.is_dir()
            else set()
        )

        for missing_name in sorted(expected_names - actual_names):
            issues.append(
                _issue(
                    "MISSING_CSV",
                    f"CSV attendu absent : {missing_name}",
                    "ERROR",
                )
            )

        for extra_name in sorted(actual_names - expected_names):
            issues.append(
                _issue(
                    "UNEXPECTED_CSV",
                    f"CSV résiduel inattendu : {extra_name}",
                    "WARNING",
                )
            )

        return issues

    def _detect_financial_images_without_tables(
        self,
        *,
        document_data: dict,
        tables: list[TableCandidate],
    ) -> list[ValidationIssue]:
        table_pages = {
            page
            for candidate in tables
            for page in candidate.pages
        }
        page_text: dict[int, list[str]] = {}

        for text_item in document_data.get("texts", []):
            text = str(text_item.get("text", ""))
            for provenance in text_item.get("prov", []):
                page = provenance.get("page_no")
                if isinstance(page, int):
                    page_text.setdefault(page, []).append(text)

        pages = document_data.get("pages", {})
        flagged_pages: set[int] = set()

        for picture in document_data.get("pictures", []):
            for provenance in picture.get("prov", []):
                page = provenance.get("page_no")
                bounding_box = provenance.get("bbox", {})
                page_data = pages.get(str(page), {})
                page_size = page_data.get("size", {})

                if not isinstance(page, int) or page in table_pages:
                    continue

                width = page_size.get("width", 0)
                height = page_size.get("height", 0)
                if width <= 0 or height <= 0:
                    continue

                picture_width = abs(
                    bounding_box.get("r", 0) - bounding_box.get("l", 0)
                )
                picture_height = abs(
                    bounding_box.get("t", 0) - bounding_box.get("b", 0)
                )
                coverage = (picture_width * picture_height) / (
                    width * height
                )
                normalized_text = " ".join(
                    page_text.get(page, [])
                ).casefold()

                if (
                    coverage >= LARGE_PICTURE_PAGE_RATIO
                    and any(
                        keyword in normalized_text
                        for keyword in FINANCIAL_PAGE_KEYWORDS
                    )
                ):
                    flagged_pages.add(page)

        return [
            _issue(
                "FINANCIAL_IMAGE_REQUIRES_OCR",
                (
                    "Une grande image financière ne possède aucun "
                    "tableau Docling associé."
                ),
                "WARNING",
                pages=[page],
            )
            for page in sorted(flagged_pages)
        ]

    def _artifact_hashes(
        self,
        output_dir: Path,
        tables: list[TableCandidate],
    ) -> dict[str, str]:
        paths = [
            output_dir / "document.md",
            output_dir / "document.json",
        ]
        paths.extend(candidate.csv_path for candidate in tables)

        return {
            str(path.relative_to(output_dir)): calculate_file_sha256(path)
            for path in paths
            if path.is_file()
        }


def is_cache_valid(
    *,
    output_dir: Path,
    metadata: dict,
    source_sha256: str,
    docling_version: str,
    backend_name: str | None = None,
    extraction_config_sha256: str | None = None,
) -> bool:
    if metadata.get("sha256") != source_sha256:
        return False

    if metadata.get("docling_version") != docling_version:
        return False

    if (
        backend_name is not None
        and metadata.get("backend") != backend_name
    ):
        return False

    if (
        extraction_config_sha256 is not None
        and metadata.get("extraction_config_sha256")
        != extraction_config_sha256
    ):
        return False

    if metadata.get("validation_schema_version") != VALIDATION_SCHEMA_VERSION:
        return False

    status = metadata.get("status")
    if status not in {
        ParserStatus.PARSED,
        ParserStatus.PARTIAL,
        ParserStatus.FAILED,
    }:
        return False

    # A validation failure is cacheable: Docling completed and downstream OCR
    # must be able to consume its empty/incomplete tables without triggering an
    # expensive identical conversion. A conversion failure remains retryable.
    if status == ParserStatus.FAILED and metadata.get("error") is not None:
        return False

    validation = metadata.get("validation")
    if not isinstance(validation, dict):
        return False

    try:
        report = ParserValidationReport.model_validate(validation)
    except ValueError:
        return False

    if report.schema_version != VALIDATION_SCHEMA_VERSION:
        return False

    expected_count = metadata.get("table_count")
    if not isinstance(expected_count, int) or expected_count < 0:
        return False
    if expected_count != len(report.tables):
        return False

    tables_dir = Path(output_dir) / "tables"
    expected_names = {
        f"table_{index:03d}.csv"
        for index in range(1, expected_count + 1)
    }
    actual_names = (
        {path.name for path in tables_dir.glob("table_*.csv")}
        if tables_dir.is_dir()
        else set()
    )
    if actual_names != expected_names:
        return False

    if not report.artifact_sha256:
        return False

    for relative_path, expected_hash in report.artifact_sha256.items():
        path = Path(output_dir) / relative_path
        if not path.is_file():
            return False
        if calculate_file_sha256(path) != expected_hash:
            return False

    expected_artifact_names = {
        "document.md",
        "document.json",
        *(f"tables/{name}" for name in expected_names),
    }
    return set(report.artifact_sha256) == expected_artifact_names
