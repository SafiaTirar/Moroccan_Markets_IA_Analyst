import argparse
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import docling
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
)
from pydantic import BaseModel, Field

from moroccan_markets_ai.data.parsers.parser_validator import (
    VALIDATION_SCHEMA_VERSION,
    ParserStatus,
    ParserValidationReport,
    ParserValidator,
    TableCandidate,
    ValidationIssue,
    is_cache_valid,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "extracted" / "ammc"
BACKEND_NAME = (
    "docling.backend.pypdfium2_backend.PyPdfiumDocumentBackend"
)
EXTRACTION_CONFIG = {
    "backend": BACKEND_NAME,
    "do_ocr": False,
    "do_table_structure": True,
}
EXTRACTION_CONFIG_SHA256 = hashlib.sha256(
    json.dumps(EXTRACTION_CONFIG, sort_keys=True).encode()
).hexdigest()


class ParsedDocument(BaseModel):
    source_pdf: str
    sha256: str
    parsed_at: str
    docling_version: str
    validation_schema_version: str
    backend: str
    extraction_config: dict[str, object]
    extraction_config_sha256: str
    conversion_seconds: float
    page_count: int
    table_count: int
    table_pages: list[list[int]] = Field(default_factory=list)
    markdown_path: str
    json_path: str
    status: ParserStatus
    validation: ParserValidationReport
    error: str | None = None


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _write_text_atomic(path: Path, content: str) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


class AMMCPDFParser:
    def __init__(self, output_root: Path = OUTPUT_ROOT):
        self.output_root = Path(output_root)
        self._converter: DocumentConverter | None = None
        self.validator = ParserValidator()

    def _get_converter(self) -> DocumentConverter:
        if self._converter is None:
            # Phase initiale : traitement des PDF textuels.
            # Les pages signalées par le validateur seront traitées par OCR.
            options = PdfPipelineOptions()
            options.do_ocr = False
            options.do_table_structure = True

            self._converter = DocumentConverter(
                allowed_formats=[InputFormat.PDF],
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=options,
                        backend=PyPdfiumDocumentBackend,
                    )
                },
            )

        return self._converter

    def parse(self, pdf_path: Path) -> ParsedDocument:
        pdf_path = Path(pdf_path).resolve()

        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)

        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("Le fichier doit être un PDF")

        with pdf_path.open("rb") as file:
            if file.read(5) != b"%PDF-":
                raise ValueError("Signature PDF invalide")

        file_hash = calculate_sha256(pdf_path)
        output_dir = (
            self.output_root / f"{pdf_path.stem}_{file_hash[:12]}"
        )
        metadata_path = output_dir / "metadata.json"

        cached = self._load_cache(
            metadata_path=metadata_path,
            output_dir=output_dir,
            file_hash=file_hash,
        )
        if cached is not None:
            logger.info("Cache utilisé : %s", pdf_path.name)
            return cached

        logger.info("Conversion Docling : %s", pdf_path.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        markdown_path = output_dir / "document.md"
        json_path = output_dir / "document.json"

        conversion_started = time.monotonic()
        try:
            result = self._get_converter().convert(str(pdf_path))
            document = result.document
        except Exception as exc:
            self._record_conversion_failure(
                pdf_path=pdf_path,
                file_hash=file_hash,
                metadata_path=metadata_path,
                markdown_path=markdown_path,
                json_path=json_path,
                error=exc,
                conversion_seconds=time.monotonic() - conversion_started,
            )
            raise RuntimeError(
                f"Échec de la conversion Docling : {pdf_path.name}"
            ) from exc

        document_dict = document.export_to_dict()
        _write_text_atomic(
            markdown_path,
            document.export_to_markdown(),
        )
        _write_text_atomic(
            json_path,
            json.dumps(
                document_dict,
                ensure_ascii=False,
                indent=2,
            ),
        )

        tables_dir = output_dir / "tables"
        tables_dir.mkdir(exist_ok=True)
        table_pages: list[list[int]] = []
        candidates: list[TableCandidate] = []

        for index, table in enumerate(document.tables, start=1):
            pages = sorted({prov.page_no for prov in table.prov})
            table_pages.append(pages)

            dataframe = table.export_to_dataframe(doc=document)
            csv_path = tables_dir / f"table_{index:03d}.csv"
            temporary_csv = csv_path.with_suffix(".csv.tmp")
            dataframe.to_csv(temporary_csv, index=False)
            temporary_csv.replace(csv_path)

            candidates.append(
                TableCandidate(
                    table_index=index,
                    pages=pages,
                    dataframe=dataframe,
                    csv_path=csv_path,
                )
            )

        expected_csv = {
            candidate.csv_path.name for candidate in candidates
        }
        for csv_path in tables_dir.glob("table_*.csv"):
            if csv_path.name not in expected_csv:
                csv_path.unlink()

        validation = self.validator.validate(
            output_dir=output_dir,
            page_count=len(document.pages),
            tables=candidates,
        )

        metadata = ParsedDocument(
            source_pdf=str(pdf_path),
            sha256=file_hash,
            parsed_at=datetime.now(UTC).isoformat(),
            docling_version=docling.__version__,
            validation_schema_version=VALIDATION_SCHEMA_VERSION,
            backend=BACKEND_NAME,
            extraction_config=EXTRACTION_CONFIG,
            extraction_config_sha256=EXTRACTION_CONFIG_SHA256,
            conversion_seconds=round(
                time.monotonic() - conversion_started,
                3,
            ),
            page_count=len(document.pages),
            table_count=len(document.tables),
            table_pages=table_pages,
            markdown_path=str(markdown_path),
            json_path=str(json_path),
            status=validation.status,
            validation=validation,
        )

        _write_text_atomic(
            metadata_path,
            metadata.model_dump_json(indent=2),
        )

        if metadata.status == ParserStatus.PARTIAL:
            logger.warning(
                "Conversion partielle : %s (%d anomalie(s))",
                pdf_path.name,
                len(validation.issues),
            )
        elif metadata.status == ParserStatus.FAILED:
            logger.error(
                "Validation échouée : %s (%d anomalie(s))",
                pdf_path.name,
                len(validation.issues),
            )

        return metadata

    def _load_cache(
        self,
        *,
        metadata_path: Path,
        output_dir: Path,
        file_hash: str,
    ) -> ParsedDocument | None:
        if not metadata_path.is_file():
            return None

        try:
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            logger.warning("Métadonnées de cache illisibles : %s", metadata_path)
            return None

        if not is_cache_valid(
            output_dir=output_dir,
            metadata=metadata,
            source_sha256=file_hash,
            docling_version=docling.__version__,
            backend_name=BACKEND_NAME,
            extraction_config_sha256=EXTRACTION_CONFIG_SHA256,
        ):
            logger.info("Cache invalide, reconversion nécessaire")
            return None

        try:
            return ParsedDocument.model_validate(metadata)
        except ValueError:
            logger.warning("Métadonnées de cache incompatibles")
            return None

    def _record_conversion_failure(
        self,
        *,
        pdf_path: Path,
        file_hash: str,
        metadata_path: Path,
        markdown_path: Path,
        json_path: Path,
        error: Exception,
        conversion_seconds: float,
    ) -> None:
        issue = ValidationIssue(
            code="CONVERSION_FAILED",
            message=str(error),
            severity="ERROR",
        )
        validation = ParserValidationReport(
            status=ParserStatus.FAILED,
            valid_table_count=0,
            incomplete_table_count=0,
            invalid_table_count=0,
            issues=[issue],
        )
        metadata = ParsedDocument(
            source_pdf=str(pdf_path),
            sha256=file_hash,
            parsed_at=datetime.now(UTC).isoformat(),
            docling_version=docling.__version__,
            validation_schema_version=VALIDATION_SCHEMA_VERSION,
            backend=BACKEND_NAME,
            extraction_config=EXTRACTION_CONFIG,
            extraction_config_sha256=EXTRACTION_CONFIG_SHA256,
            conversion_seconds=round(conversion_seconds, 3),
            page_count=0,
            table_count=0,
            markdown_path=str(markdown_path),
            json_path=str(json_path),
            status=ParserStatus.FAILED,
            validation=validation,
            error=str(error),
        )
        _write_text_atomic(
            metadata_path,
            metadata.model_dump_json(indent=2),
        )


def main():
    cli = argparse.ArgumentParser(
        description="Parser de rapports financiers AMMC"
    )
    cli.add_argument(
        "pdf",
        type=Path,
        help="Chemin vers un rapport PDF",
    )

    args = cli.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s - %(message)s",
    )

    parser = AMMCPDFParser()
    result = parser.parse(args.pdf)

    print("\n=== RÉSULTAT DU PARSING ===")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
