"""Collecte reproductible de l'IPC mensuel publié par le HCP."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "hcp"
SOURCE_PAGE_URL = (
    "https://www.hcp.ma/"
    "Indice-des-prix-a-la-consommation-IPC-2017-par-grandes-divisions-"
    "Mensuel_a2513.html"
)
RAW_WORKBOOK = RAW_DIR / "hcp_ipc_base_2017_monthly.xlsx"
SOURCE_SNAPSHOT = RAW_DIR / "source_page.html"
METADATA_FILE = RAW_DIR / "metadata.json"
MAX_ATTEMPTS = 5
USER_AGENT = "MoroccanMarketsAIAnalyst/0.1 (academic research)"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, payload: dict) -> None:
    atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def request_with_retries(
    client: httpx.Client,
    url: str,
) -> httpx.Response:
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 and exc.response.status_code != 429:
                raise
            if attempt + 1 == MAX_ATTEMPTS:
                raise
        except httpx.TransportError:
            if attempt + 1 == MAX_ATTEMPTS:
                raise
        time.sleep(2**attempt)
    raise RuntimeError("Nombre de tentatives HTTP HCP épuisé")


def discover_export_url(html: str) -> str:
    """Extrait uniquement un classeur Google Sheets lié par la page HCP."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = [
        str(anchor["href"])
        for anchor in soup.select("a[href]")
        if "docs.google.com/spreadsheets/" in str(anchor["href"])
        and "/export" in str(anchor["href"])
    ]
    if not candidates:
        raise ValueError("Lien d'export Google Sheets absent de la page HCP")

    parsed = urlparse(candidates[0])
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com":
        raise ValueError("Domaine d'export HCP inattendu")
    match = parsed.path.split("/spreadsheets/d/", maxsplit=1)
    if len(match) != 2 or "/export" not in match[1]:
        raise ValueError("Identifiant Google Sheets HCP invalide")

    query = parse_qs(parsed.query)
    normalized_query = {
        "format": "xlsx",
        "gid": query.get("gid", ["0"])[0],
    }
    return urlunparse(parsed._replace(query=urlencode(normalized_query)))


def is_valid_xlsx(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            return "[Content_Types].xml" in names and "xl/workbook.xml" in names
    except (OSError, zipfile.BadZipFile):
        return False


def download_workbook(
    client: httpx.Client,
    export_url: str,
    destination: Path,
    *,
    refresh: bool = False,
) -> tuple[str, httpx.Response | None]:
    if destination.is_file() and is_valid_xlsx(destination) and not refresh:
        return "CACHED", None

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    response = request_with_retries(client, export_url)
    content_type = response.headers.get("content-type", "").lower()
    if "spreadsheetml" not in content_type and not response.content.startswith(b"PK"):
        raise ValueError(f"Type de contenu Excel inattendu : {content_type}")
    temporary.write_bytes(response.content)
    if not is_valid_xlsx(temporary):
        raise ValueError("Le fichier HCP reçu n'est pas un classeur XLSX valide")
    temporary.replace(destination)
    return "DOWNLOADED", response


def collect_hcp_ipc(
    *,
    output_dir: Path = RAW_DIR,
    refresh: bool = False,
    client: httpx.Client | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    workbook = output_dir / RAW_WORKBOOK.name
    snapshot = output_dir / SOURCE_SNAPSHOT.name
    metadata_path = output_dir / METADATA_FILE.name
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(120, connect=20),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        page_response = request_with_retries(active_client, SOURCE_PAGE_URL)
        export_url = discover_export_url(page_response.text)
        atomic_text(snapshot, page_response.text)
        status, workbook_response = download_workbook(
            active_client,
            export_url,
            workbook,
            refresh=refresh,
        )
    finally:
        if owns_client:
            active_client.close()

    previous = {}
    if metadata_path.is_file():
        previous = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata = {
        "schema_version": "1.0",
        "dataset": "HCP IPC 2017 par grandes divisions - mensuel",
        "publisher": "Haut-Commissariat au Plan du Maroc",
        "source_page_url": SOURCE_PAGE_URL,
        "export_url": export_url,
        "final_download_url": (
            str(workbook_response.url)
            if workbook_response is not None
            else previous.get("final_download_url", "")
        ),
        "collected_at": (
            utc_now() if status == "DOWNLOADED" else previous.get("collected_at", "")
        ),
        "checked_at": utc_now(),
        "status": status,
        "raw_file": str(workbook.resolve()),
        "source_snapshot": str(snapshot.resolve()),
        "content_type": (
            workbook_response.headers.get("content-type", "")
            if workbook_response is not None
            else previous.get("content_type", "")
        ),
        "size_bytes": workbook.stat().st_size,
        "sha256": sha256_file(workbook),
        "authenticity": {
            "source_page_host": "www.hcp.ma",
            "export_host": "docs.google.com",
            "export_discovered_on_source_page": True,
        },
    }
    atomic_json(metadata_path, metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=RAW_DIR)
    args = parser.parse_args()
    result = collect_hcp_ipc(output_dir=args.output_dir, refresh=args.refresh)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
