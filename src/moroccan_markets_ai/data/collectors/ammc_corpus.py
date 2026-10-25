"""Construction reproductible du corpus financier AMMC 2023-2025."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from moroccan_markets_ai.data.collectors.ammc_downloader import is_pdf

BASE_URL = "https://www.ammc.ma"
AJAX_URL = f"{BASE_URL}/fr/views/ajax"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "ammc"
CORPUS_ROOT = RAW_ROOT / "corpus"
LISTING_CACHE = CORPUS_ROOT / "cache" / "listings"
DETAIL_CACHE = CORPUS_ROOT / "cache" / "details"
PDF_ROOT = RAW_ROOT / "pdfs" / "annual"
SELECTION_FILE = PROJECT_ROOT / "config" / "data" / "ammc_corpus.json"

DISCOVERY_FILE = CORPUS_ROOT / "discovered_reports.csv"
INVENTORY_FILE = CORPUS_ROOT / "corpus_inventory.csv"
MANIFEST_FILE = CORPUS_ROOT / "download_manifest.csv"
COVERAGE_CSV = CORPUS_ROOT / "coverage.csv"
COVERAGE_JSON = CORPUS_ROOT / "coverage.json"

YEARS = (2023, 2024, 2025)
REQUEST_DELAY_SECONDS = 0.35
MAX_HTTP_ATTEMPTS = 5
USER_AGENT = (
    "MoroccanMarketsAIAnalyst/0.1 "
    "(academic research; reproducible AMMC corpus)"
)

VIEW_DATA = {
    "view_name": "liste_etats_financiers_emetteurs",
    "view_display_id": "page_1",
    "view_args": "",
    "view_path": "/liste-etats-financiers-emetteurs",
    "view_base_path": "liste-etats-financiers-emetteurs",
    "pager_element": "0",
    "_drupal_ajax": "1",
}

DISCOVERY_COLUMNS = [
    "issuer",
    "exercise_year",
    "report_type",
    "report_family",
    "detail_url",
    "listing_page",
    "discovery_method",
    "discovered_at",
]

INVENTORY_COLUMNS = [
    "company_id",
    "issuer",
    "sector",
    "exercise_year",
    "report_type",
    "report_family",
    "perimeter",
    "detail_url",
    "detail_title",
    "detail_issuer",
    "detail_year",
    "detail_report_type",
    "pdf_count",
    "pdf_url",
    "metadata_status",
    "metadata_issues",
]

MANIFEST_COLUMNS = [
    *INVENTORY_COLUMNS,
    "local_path",
    "downloaded_at",
    "sha256",
    "size_bytes",
    "download_status",
    "duplicate_of",
    "error",
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")


def normalize_identity(value: str) -> str:
    tokens = slugify(value).split("-")
    legal = {"sa", "sarl", "societe", "ste", "groupe", "group"}
    return "-".join(token for token in tokens if token not in legal)


def report_family(label: str) -> str:
    normalized = slugify(label)
    if "annuel" in normalized or "rfa" in normalized:
        return "ANNUAL"
    if "semestre" in normalized or "rfs" in normalized:
        return "SEMIANNUAL"
    return "OTHER"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def atomic_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ajax_fragment(payload: list[dict[str, Any]]) -> str:
    for command in payload:
        if command.get("command") == "insert" and command.get("data"):
            return str(command["data"])
    raise ValueError("La réponse AJAX AMMC ne contient pas de fragment de résultats")


def fetch_ajax_fragment(
    client: httpx.Client,
    *,
    params: dict[str, str],
) -> str:
    for attempt in range(MAX_HTTP_ATTEMPTS):
        try:
            response = client.post(
                AJAX_URL,
                params=params,
                data=VIEW_DATA,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            return ajax_fragment(response.json())
        except (httpx.HTTPError, ValueError):
            if attempt + 1 == MAX_HTTP_ATTEMPTS:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Nombre de tentatives HTTP AMMC épuisé")


def parse_year_terms(fragment: str) -> dict[int, str]:
    soup = BeautifulSoup(fragment, "html.parser")
    select = soup.select_one("select[name=field_annee_value_1]")
    if select is None:
        raise ValueError("Filtre des exercices AMMC introuvable")
    terms: dict[int, str] = {}
    for option in select.select("option[value]"):
        label = option.get_text(" ", strip=True)
        if re.fullmatch(r"20\d{2}", label):
            terms[int(label)] = str(option["value"])
    return terms


def parse_listing_fragment(
    fragment: str,
    *,
    page: int,
    collected_at: str,
) -> tuple[list[dict[str, Any]], int]:
    soup = BeautifulSoup(fragment, "html.parser")
    rows: list[dict[str, Any]] = []
    for table_row in soup.select("table tbody tr"):
        issuer_cell = table_row.select_one(".views-field-field-emetteur")
        year_cell = table_row.select_one(".views-field-field-annee")
        type_cell = table_row.select_one(".views-field-field-type-rapp-ef-em")
        detail_anchor = (
            type_cell.select_one("a[href*='/etats-financiers/']")
            if type_cell
            else None
        )
        if not all((issuer_cell, year_cell, type_cell, detail_anchor)):
            continue
        issuer_anchor = issuer_cell.select_one("a[href*='/espace-emetteurs/']")
        issuer = (
            issuer_anchor.get_text(" ", strip=True)
            if issuer_anchor
            else issuer_cell.get_text(" ", strip=True)
        )
        year_match = re.search(r"20\d{2}", year_cell.get_text(" ", strip=True))
        if not year_match:
            continue
        type_label = type_cell.get_text(" ", strip=True)
        rows.append(
            {
                "issuer": issuer,
                "exercise_year": int(year_match.group()),
                "report_type": type_label,
                "report_family": report_family(type_label),
                "detail_url": urljoin(BASE_URL, detail_anchor["href"]),
                "listing_page": page,
                "discovery_method": "CURRENT_AJAX_LISTING",
                "discovered_at": collected_at,
            }
        )

    max_page = page
    for anchor in soup.select(".pager a[href]"):
        query = parse_qs(urlparse(anchor["href"]).query)
        if query.get("page", [""])[0].isdigit():
            max_page = max(max_page, int(query["page"][0]))
    return rows, max_page


def discover_reports(
    client: httpx.Client,
    *,
    years: Iterable[int] = YEARS,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    bootstrap_path = LISTING_CACHE / "filters.html"
    if bootstrap_path.is_file() and not refresh:
        bootstrap = bootstrap_path.read_text(encoding="utf-8")
    else:
        bootstrap = fetch_ajax_fragment(client, params={"page": "0"})
        bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_path.write_text(bootstrap, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)
    year_terms = parse_year_terms(bootstrap)

    discovered: list[dict[str, Any]] = []
    collected_at = utc_now()
    for year in years:
        if year not in year_terms:
            raise ValueError(f"Exercice {year} absent des filtres AMMC")
        page = 0
        max_page = 0
        while page <= max_page:
            cache_path = LISTING_CACHE / str(year) / f"page_{page:03d}.html"
            if cache_path.is_file() and not refresh:
                fragment = cache_path.read_text(encoding="utf-8")
            else:
                fragment = fetch_ajax_fragment(
                    client,
                    params={
                        "field_annee_value_1": year_terms[year],
                        "field_emetteur_target_id_verf": "All",
                        "page": str(page),
                    },
                )
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(fragment, encoding="utf-8")
                time.sleep(REQUEST_DELAY_SECONDS)
            rows, reported_max = parse_listing_fragment(
                fragment,
                page=page,
                collected_at=collected_at,
            )
            discovered.extend(row for row in rows if row["exercise_year"] == year)
            max_page = max(max_page, reported_max)
            page += 1

    unique = {
        (row["detail_url"], row["exercise_year"], row["report_type"]): row
        for row in discovered
    }
    rows = sorted(
        unique.values(),
        key=lambda row: (
            normalize_identity(str(row["issuer"])),
            int(row["exercise_year"]),
            str(row["report_family"]),
        ),
    )
    atomic_csv(DISCOVERY_FILE, rows, DISCOVERY_COLUMNS)
    return rows


def parse_detail_page(html: str, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1")
    fields: dict[str, str] = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        key = slugify(cells[0].get_text(" ", strip=True))
        if key:
            fields[key] = cells[1].get_text(" ", strip=True)

    pdf_urls = list(
        dict.fromkeys(
            urljoin(BASE_URL, anchor["href"])
            for anchor in soup.select("a[href]")
            if urlparse(anchor["href"]).path.lower().endswith(".pdf")
        )
    )
    year_match = re.search(r"20\d{2}", fields.get("annee", ""))
    return {
        "detail_title": title.get_text(" ", strip=True) if title else "",
        "detail_issuer": fields.get("emetteur", ""),
        "detail_year": int(year_match.group()) if year_match else None,
        "detail_report_type": fields.get("rapports-financiers", ""),
        "pdf_urls": pdf_urls,
        "source_url": source_url,
    }


def fetch_detail(
    client: httpx.Client,
    url: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_path = DETAIL_CACHE / f"{slugify(urlparse(url).path)}.html"
    if cache_path.is_file() and not refresh:
        html = cache_path.read_text(encoding="utf-8")
    else:
        for attempt in range(MAX_HTTP_ATTEMPTS):
            try:
                response = client.get(url)
                response.raise_for_status()
                html = response.text
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise
                if attempt + 1 == MAX_HTTP_ATTEMPTS:
                    raise
                time.sleep(2**attempt)
            except httpx.TransportError:
                if attempt + 1 == MAX_HTTP_ATTEMPTS:
                    raise
                time.sleep(2**attempt)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(html, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)
    return parse_detail_page(html, url)


def load_selection(path: Path = SELECTION_FILE) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    companies = payload.get("companies", [])
    if len(companies) != 15:
        raise ValueError("La sélection AMMC doit contenir exactement 15 entreprises")
    ids = [str(item["company_id"]) for item in companies]
    if len(ids) != len(set(ids)):
        raise ValueError("Les company_id de la sélection AMMC doivent être uniques")
    return companies


def annual_candidate_urls(source_url: str, target_year: int) -> list[str]:
    parsed = urlparse(source_url)
    replaced = re.sub(
        r"(?i)(rfa(?:-[a-z]+)*-)(20\d{2})(?=$|/)",
        rf"\g<1>{target_year}",
        parsed.path,
    )
    if replaced == parsed.path:
        return []
    return [
        f"https://www.ammc.ma{replaced}",
        f"https://v8.ammc.ma{replaced}",
    ]


def dated_candidate_urls(source_url: str, target_year: int) -> list[str]:
    parsed = urlparse(source_url)
    replaced = re.sub(r"20\d{2}", str(target_year), parsed.path)
    if replaced == parsed.path:
        return []
    return [
        f"https://www.ammc.ma{replaced}",
        f"https://v8.ammc.ma{replaced}",
    ]


def match_selected_company(
    issuer: str,
    companies: list[dict[str, str]],
) -> dict[str, str] | None:
    normalized = normalize_identity(issuer)
    for company in companies:
        aliases = [company["ammc_issuer"], *company.get("aliases", [])]
        if normalized in {normalize_identity(alias) for alias in aliases}:
            return company
    return None


def complete_selected_annual_discovery(
    client: httpx.Client,
    discovered: list[dict[str, Any]],
    companies: list[dict[str, str]],
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Complète une année absente uniquement après validation de la fiche officielle."""
    rows = list(discovered)
    for row in rows:
        row["discovery_method"] = (
            row.get("discovery_method") or "CURRENT_AJAX_LISTING"
        )
    for company in companies:
        company_rows = [
            row
            for row in rows
            if match_selected_company(str(row["issuer"]), [company])
            and row["report_family"] == "ANNUAL"
        ]
        present_years = {int(row["exercise_year"]) for row in company_rows}
        sources = sorted(
            company_rows,
            key=lambda row: int(row["exercise_year"]),
            reverse=True,
        )
        for year in YEARS:
            if year in present_years:
                continue
            accepted = False
            candidates: list[str] = []
            for source in sources:
                candidates.extend(annual_candidate_urls(str(source["detail_url"]), year))
            for candidate in dict.fromkeys(candidates):
                try:
                    detail = fetch_detail(client, candidate, refresh=refresh)
                except (httpx.HTTPError, OSError, ValueError):
                    continue
                aliases = [company["ammc_issuer"], *company.get("aliases", [])]
                issuer_ok = normalize_identity(str(detail["detail_issuer"])) in {
                    normalize_identity(alias) for alias in aliases
                }
                if not (
                    issuer_ok
                    and detail["detail_year"] == year
                    and report_family(str(detail["detail_report_type"])) == "ANNUAL"
                    and detail["pdf_urls"]
                ):
                    continue
                rows.append(
                    {
                        "issuer": detail["detail_issuer"],
                        "exercise_year": year,
                        "report_type": detail["detail_report_type"],
                        "report_family": "ANNUAL",
                        "detail_url": candidate,
                        "listing_page": "",
                        "discovery_method": "OFFICIAL_DETAIL_VERIFIED",
                        "discovered_at": utc_now(),
                    }
                )
                present_years.add(year)
                accepted = True
                break
            if accepted:
                continue
    unique = {
        (row["detail_url"], int(row["exercise_year"]), row["report_type"]): row
        for row in rows
    }
    completed = sorted(
        unique.values(),
        key=lambda row: (
            normalize_identity(str(row["issuer"])),
            int(row["exercise_year"]),
            str(row["report_family"]),
        ),
    )
    atomic_csv(DISCOVERY_FILE, completed, DISCOVERY_COLUMNS)
    return completed


def complete_selected_semianual_discovery(
    client: httpx.Client,
    discovered: list[dict[str, Any]],
    companies: list[dict[str, str]],
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Recherche les semestres absents et n'admet que les fiches AMMC validées."""
    rows = list(discovered)
    for company in companies:
        company_rows = [
            row
            for row in rows
            if match_selected_company(str(row["issuer"]), [company])
            and row["report_family"] == "SEMIANNUAL"
        ]
        present_years = {int(row["exercise_year"]) for row in company_rows}
        for year in YEARS:
            if year in present_years:
                continue
            candidates: list[str] = []
            for source in company_rows:
                candidates.extend(dated_candidate_urls(str(source["detail_url"]), year))
            for candidate in dict.fromkeys(candidates):
                try:
                    detail = fetch_detail(client, candidate, refresh=refresh)
                except (httpx.HTTPError, OSError, ValueError):
                    continue
                aliases = [company["ammc_issuer"], *company.get("aliases", [])]
                issuer_ok = normalize_identity(str(detail["detail_issuer"])) in {
                    normalize_identity(alias) for alias in aliases
                }
                if not (
                    issuer_ok
                    and detail["detail_year"] == year
                    and report_family(str(detail["detail_report_type"])) == "SEMIANNUAL"
                    and detail["pdf_urls"]
                ):
                    continue
                rows.append(
                    {
                        "issuer": detail["detail_issuer"],
                        "exercise_year": year,
                        "report_type": detail["detail_report_type"],
                        "report_family": "SEMIANNUAL",
                        "detail_url": candidate,
                        "listing_page": "",
                        "discovery_method": "OFFICIAL_DETAIL_VERIFIED",
                        "discovered_at": utc_now(),
                    }
                )
                present_years.add(year)
                break
    unique = {
        (row["detail_url"], int(row["exercise_year"]), row["report_type"]): row
        for row in rows
    }
    completed = sorted(
        unique.values(),
        key=lambda row: (
            normalize_identity(str(row["issuer"])),
            int(row["exercise_year"]),
            str(row["report_family"]),
        ),
    )
    atomic_csv(DISCOVERY_FILE, completed, DISCOVERY_COLUMNS)
    return completed


def metadata_validation(
    listing: dict[str, Any],
    detail: dict[str, Any],
    pdf_url: str,
) -> tuple[str, list[str]]:
    issues: list[str] = []
    listed_issuer = normalize_identity(str(listing["issuer"]))
    detailed_issuer = normalize_identity(str(detail["detail_issuer"]))
    if not detailed_issuer or listed_issuer != detailed_issuer:
        issues.append("ISSUER_MISMATCH")
    if detail["detail_year"] != int(listing["exercise_year"]):
        issues.append("EXERCISE_YEAR_MISMATCH")
    if report_family(str(detail["detail_report_type"])) != listing["report_family"]:
        issues.append("REPORT_TYPE_MISMATCH")

    filename = unquote(Path(urlparse(pdf_url).path).name)
    filename_years = {int(year) for year in re.findall(r"20\d{2}", filename)}
    if filename_years and int(listing["exercise_year"]) not in filename_years:
        issues.append("PDF_FILENAME_YEAR_MISMATCH")
    elif not filename_years:
        issues.append("PDF_FILENAME_YEAR_NOT_EXPLICIT")

    hard_mismatches = [issue for issue in issues if not issue.endswith("NOT_EXPLICIT")]
    if hard_mismatches:
        return "MISMATCH", issues
    if issues:
        return "UNCERTAIN", issues
    return "CONSISTENT", issues


def build_inventory(
    client: httpx.Client,
    discovered: list[dict[str, Any]],
    companies: list[dict[str, str]],
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    selected_rows = [
        (row, match_selected_company(str(row["issuer"]), companies))
        for row in discovered
    ]
    for row, company in selected_rows:
        if company is None:
            continue
        base = {
            "company_id": company["company_id"],
            "issuer": row["issuer"],
            "sector": company["sector"],
            "exercise_year": row["exercise_year"],
            "report_type": row["report_type"],
            "report_family": row["report_family"],
            "perimeter": "UNKNOWN",
            "detail_url": row["detail_url"],
        }
        try:
            detail = fetch_detail(client, str(row["detail_url"]), refresh=refresh)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            inventory.append(
                {
                    **base,
                    "detail_title": "",
                    "detail_issuer": "",
                    "detail_year": "",
                    "detail_report_type": "",
                    "pdf_count": 0,
                    "pdf_url": "",
                    "metadata_status": "FETCH_FAILED",
                    "metadata_issues": str(exc),
                }
            )
            continue

        pdf_urls = detail["pdf_urls"]
        if not pdf_urls:
            inventory.append(
                {
                    **base,
                    **{key: detail[key] for key in (
                        "detail_title",
                        "detail_issuer",
                        "detail_year",
                        "detail_report_type",
                    )},
                    "pdf_count": 0,
                    "pdf_url": "",
                    "metadata_status": "MISSING_ATTACHMENT",
                    "metadata_issues": "PDF_ATTACHMENT_NOT_FOUND",
                }
            )
            continue

        for pdf_url in pdf_urls:
            status, issues = metadata_validation(row, detail, pdf_url)
            if len(pdf_urls) > 1:
                issues.append("MULTIPLE_PDF_ATTACHMENTS")
                if status == "CONSISTENT":
                    status = "UNCERTAIN"
            inventory.append(
                {
                    **base,
                    **{key: detail[key] for key in (
                        "detail_title",
                        "detail_issuer",
                        "detail_year",
                        "detail_report_type",
                    )},
                    "pdf_count": len(pdf_urls),
                    "pdf_url": pdf_url,
                    "metadata_status": status,
                    "metadata_issues": "|".join(issues),
                }
            )

    inventory.sort(
        key=lambda row: (
            str(row["company_id"]),
            int(row["exercise_year"]),
            str(row["report_family"]),
            str(row["pdf_url"]),
        )
    )
    atomic_csv(INVENTORY_FILE, inventory, INVENTORY_COLUMNS)
    return inventory


def safe_pdf_filename(url: str) -> str:
    filename = Path(unquote(urlparse(url).path)).name
    if not filename.lower().endswith(".pdf"):
        raise ValueError(f"URL sans nom PDF : {url}")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", filename)


def download_with_resume(
    client: httpx.Client,
    url: str,
    destination: Path,
) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and is_pdf(destination):
        return "CACHED"
    if destination.exists():
        raise ValueError(f"Fichier final invalide : {destination}")

    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.is_file() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    mode = "ab" if offset else "wb"
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        if offset and response.status_code != 206:
            offset = 0
            mode = "wb"
        with partial.open(mode) as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                handle.write(chunk)

    if not is_pdf(partial):
        raise ValueError("Le contenu téléchargé n'est pas un PDF")
    partial.replace(destination)
    return "RESUMED" if offset else "DOWNLOADED"


def known_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted((RAW_ROOT / "pdfs").rglob("*.pdf")):
        try:
            hashes.setdefault(file_sha256(path), str(path.relative_to(PROJECT_ROOT)))
        except OSError:
            continue
    return hashes


def download_annual_reports(
    client: httpx.Client,
    inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous = {
        (row["company_id"], row["exercise_year"], row["pdf_url"]): row
        for row in read_csv(MANIFEST_FILE)
    }
    hashes = known_hashes()
    manifest: list[dict[str, Any]] = []
    annual = [
        row
        for row in inventory
        if row["report_family"] == "ANNUAL" and row["pdf_url"]
    ]
    seen_urls: set[str] = set()
    for row in annual:
        key = (str(row["company_id"]), str(row["exercise_year"]), str(row["pdf_url"]))
        if str(row["pdf_url"]) in seen_urls:
            continue
        seen_urls.add(str(row["pdf_url"]))
        destination = (
            PDF_ROOT
            / str(row["company_id"])
            / str(row["exercise_year"])
            / safe_pdf_filename(str(row["pdf_url"]))
        )
        entry = {
            **row,
            "local_path": str(destination.relative_to(PROJECT_ROOT)),
            "downloaded_at": "",
            "sha256": "",
            "size_bytes": 0,
            "download_status": "",
            "duplicate_of": "",
            "error": "",
        }
        old = previous.get(key)
        if old and destination.is_file() and is_pdf(destination):
            entry.update(
                {
                    "downloaded_at": old.get("downloaded_at", ""),
                    "download_status": "CACHED",
                }
            )
        try:
            status = download_with_resume(client, str(row["pdf_url"]), destination)
            digest = file_sha256(destination)
            relative = str(destination.relative_to(PROJECT_ROOT))
            duplicate = hashes.get(digest, "")
            entry.update(
                {
                    "download_status": (
                        "DUPLICATE_CONTENT" if duplicate and duplicate != relative else status
                    ),
                    "downloaded_at": (
                        entry["downloaded_at"]
                        or (utc_now() if status != "CACHED" else "")
                    ),
                    "sha256": digest,
                    "size_bytes": destination.stat().st_size,
                    "duplicate_of": duplicate if duplicate != relative else "",
                }
            )
            hashes.setdefault(digest, relative)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            entry["download_status"] = "FAILED"
            entry["error"] = str(exc)
        manifest.append(entry)
        atomic_csv(MANIFEST_FILE, manifest, MANIFEST_COLUMNS)
        if entry["download_status"] not in {"CACHED", "DUPLICATE_CONTENT"}:
            time.sleep(REQUEST_DELAY_SECONDS)
    return manifest


def coverage_report(
    companies: list[dict[str, str]],
    inventory: list[dict[str, Any]],
    manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    annual_by_key: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    semi_by_key: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        key = (str(row["company_id"]), int(row["exercise_year"]))
        target = annual_by_key if row["report_family"] == "ANNUAL" else semi_by_key
        if row["report_family"] in {"ANNUAL", "SEMIANNUAL"}:
            target[key].append(row)
    downloads = {
        (str(row["company_id"]), int(row["exercise_year"])): row
        for row in manifest
        if row.get("download_status")
    }

    matrix: list[dict[str, Any]] = []
    for company in companies:
        for year in YEARS:
            key = (company["company_id"], year)
            annual_rows = annual_by_key.get(key, [])
            semi_rows = semi_by_key.get(key, [])
            downloaded = downloads.get(key)
            if not annual_rows:
                annual_status = "MISSING"
            elif any(row["metadata_status"] == "MISMATCH" for row in annual_rows):
                annual_status = "UNCERTAIN"
            elif downloaded:
                annual_status = downloaded["download_status"]
            else:
                annual_status = "FOUND_NOT_DOWNLOADED"
            matrix.append(
                {
                    "company_id": company["company_id"],
                    "issuer": company["ammc_issuer"],
                    "sector": company["sector"],
                    "exercise_year": year,
                    "annual_status": annual_status,
                    "annual_documents": len(annual_rows),
                    "semiannual_documents": len(semi_rows),
                    "metadata_status": "|".join(
                        sorted({str(row["metadata_status"]) for row in annual_rows})
                    ),
                }
            )
    atomic_csv(
        COVERAGE_CSV,
        matrix,
        [
            "company_id",
            "issuer",
            "sector",
            "exercise_year",
            "annual_status",
            "annual_documents",
            "semiannual_documents",
            "metadata_status",
        ],
    )
    statuses = Counter(str(row["annual_status"]) for row in matrix)
    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "source": "AMMC",
        "source_url": f"{BASE_URL}/fr/liste-etats-financiers-emetteurs",
        "target": {
            "companies": len(companies),
            "years": list(YEARS),
            "expected_annual_reports": len(companies) * len(YEARS),
        },
        "summary": {
            "annual_by_status": dict(sorted(statuses.items())),
            "annual_found": sum(bool(row["annual_documents"]) for row in matrix),
            "annual_downloaded_or_cached": sum(
                row["annual_status"]
                in {"DOWNLOADED", "RESUMED", "CACHED", "DUPLICATE_CONTENT"}
                for row in matrix
            ),
            "annual_missing": statuses["MISSING"],
            "annual_uncertain": statuses["UNCERTAIN"],
            "semiannual_documents": sum(
                int(row["semiannual_documents"]) for row in matrix
            ),
        },
        "companies": companies,
        "coverage": matrix,
        "limitations": [
            "Le périmètre social/consolidé reste UNKNOWN tant que le PDF n'est pas analysé.",
            "L'exercice provient du champ AMMC, pas de la date de publication.",
            "Les rapports semestriels sont inventoriés mais ne sont pas téléchargés par défaut.",
        ],
    }
    atomic_json(COVERAGE_JSON, payload)
    return payload


def new_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(90, connect=20),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("discover", "inventory", "download", "all"),
        default="all",
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--selection", type=Path, default=SELECTION_FILE)
    args = parser.parse_args()

    with new_client() as client:
        if args.stage in {"discover", "all"}:
            discovered = discover_reports(client, refresh=args.refresh)
            print(f"Fiches découvertes : {len(discovered)}")
            if args.stage == "discover":
                return
        else:
            discovered = read_csv(DISCOVERY_FILE)

        companies = load_selection(args.selection)
        if args.stage in {"inventory", "all"}:
            discovered = complete_selected_annual_discovery(
                client,
                discovered,
                companies,
                refresh=args.refresh,
            )
            discovered = complete_selected_semianual_discovery(
                client,
                discovered,
                companies,
                refresh=args.refresh,
            )
            inventory = build_inventory(
                client,
                discovered,
                companies,
                refresh=args.refresh,
            )
            print(f"Documents sélectionnés : {len(inventory)}")
            if args.stage == "inventory":
                report = coverage_report(companies, inventory, read_csv(MANIFEST_FILE))
                print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
                return
        else:
            inventory = read_csv(INVENTORY_FILE)

        manifest = download_annual_reports(client, inventory)
        report = coverage_report(companies, inventory, manifest)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
