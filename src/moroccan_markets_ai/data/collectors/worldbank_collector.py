"""Collecte reproductible d'indicateurs annuels officiels World Bank."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.worldbank.org/v2"
COUNTRY = "MAR"
START_YEAR = 2010
END_YEAR = 2025
MAX_ATTEMPTS = 5
USER_AGENT = "MoroccanMarketsAIAnalyst/0.1 (academic research)"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "worldbank"
MANIFEST_FILE = RAW_DIR / "manifest.json"
COLLECTOR_SCHEMA_VERSION = "1.0"

INDICATORS: dict[str, dict[str, str]] = {
    "NY.GDP.MKTP.KD.ZG": {
        "indicator": "gdp_growth_annual_pct",
        "official_name": "GDP growth (annual %)",
        "unit": "percent",
        "raw_filename": "gdp_growth_annual_pct.json",
    },
    "FP.CPI.TOTL.ZG": {
        "indicator": "inflation_consumer_prices_annual_pct",
        "official_name": "Inflation, consumer prices (annual %)",
        "unit": "percent",
        "raw_filename": "inflation_consumer_prices_annual_pct.json",
    },
}


class WorldBankAPIError(RuntimeError):
    """Réponse réseau ou API ne permettant pas une collecte fiable."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_bytes(path, content.encode("utf-8"))


def indicator_url(code: str, start_year: int, end_year: int) -> str:
    return (
        f"{BASE_URL}/country/{COUNTRY}/indicator/{code}"
        f"?format=json&date={start_year}%3A{end_year}&per_page=1000"
    )


def request_with_retries(client: httpx.Client, url: str) -> httpx.Response:
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status < 500 and status != 429:
                raise WorldBankAPIError(
                    f"Erreur API World Bank HTTP {status}: {url}"
                ) from exc
            if attempt + 1 == MAX_ATTEMPTS:
                raise WorldBankAPIError(
                    f"API World Bank indisponible après {MAX_ATTEMPTS} tentatives"
                ) from exc
        except httpx.TransportError as exc:
            if attempt + 1 == MAX_ATTEMPTS:
                raise WorldBankAPIError(
                    f"Échec réseau World Bank après {MAX_ATTEMPTS} tentatives"
                ) from exc
        time.sleep(2**attempt)
    raise WorldBankAPIError("Nombre de tentatives World Bank épuisé")


def validate_payload(payload: Any, indicator_code: str) -> tuple[dict, list[dict]]:
    if (
        not isinstance(payload, list)
        or len(payload) != 2
        or not isinstance(payload[0], dict)
        or not isinstance(payload[1], list)
    ):
        raise WorldBankAPIError(
            f"Structure de réponse inattendue pour {indicator_code}"
        )
    metadata, records = payload
    try:
        pages = int(metadata.get("pages", 0))
        total = int(metadata.get("total", -1))
    except (TypeError, ValueError) as exc:
        raise WorldBankAPIError("Métadonnées de pagination invalides") from exc
    if pages != 1:
        raise WorldBankAPIError(
            f"Pagination inattendue ({pages} pages) pour {indicator_code}"
        )
    if not records or total != len(records):
        raise WorldBankAPIError(
            f"Nombre d'observations incohérent pour {indicator_code}"
        )
    for record in records:
        if not isinstance(record, dict):
            raise WorldBankAPIError("Observation World Bank non structurée")
        record_code = (record.get("indicator") or {}).get("id")
        if record_code != indicator_code:
            raise WorldBankAPIError(
                f"Indicateur inattendu dans la réponse : {record_code}"
            )
        if record.get("countryiso3code") != COUNTRY:
            raise WorldBankAPIError(
                f"Pays inattendu dans la réponse : {record.get('countryiso3code')}"
            )
    return metadata, records


def decode_response(response: httpx.Response, indicator_code: str) -> tuple[dict, list[dict]]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WorldBankAPIError(
            f"Réponse non JSON pour {indicator_code}"
        ) from exc
    return validate_payload(payload, indicator_code)


def cache_is_valid(
    entry: dict[str, Any] | None,
    raw_path: Path,
    *,
    indicator_code: str,
    url: str,
) -> bool:
    if not entry or entry.get("url") != url or not raw_path.is_file():
        return False
    if entry.get("sha256") != sha256_file(raw_path):
        return False
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        validate_payload(payload, indicator_code)
    except (OSError, json.JSONDecodeError, WorldBankAPIError):
        return False
    return True


def fetch_indicator(
    indicator_code: str,
    *,
    client: httpx.Client | None = None,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
) -> list[dict]:
    """Retourne les observations API; conservé comme interface simple réutilisable."""
    if indicator_code not in INDICATORS:
        raise ValueError(f"Indicateur World Bank non autorisé : {indicator_code}")
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(60, connect=20),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        response = request_with_retries(
            active_client,
            indicator_url(indicator_code, start_year, end_year),
        )
        _, records = decode_response(response, indicator_code)
        return records
    finally:
        if owns_client:
            active_client.close()


def collect_worldbank(
    *,
    output_dir: Path = RAW_DIR,
    refresh: bool = False,
    client: httpx.Client | None = None,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
) -> dict[str, Any]:
    if start_year > end_year:
        raise ValueError("La première année doit précéder la dernière")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_FILE.name
    previous: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    previous_entries = {
        entry.get("indicator_code"): entry
        for entry in previous.get("indicators", [])
        if isinstance(entry, dict)
    }

    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(60, connect=20),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    entries: list[dict[str, Any]] = []
    try:
        for code, definition in INDICATORS.items():
            url = indicator_url(code, start_year, end_year)
            raw_path = output_dir / definition["raw_filename"]
            cached = not refresh and cache_is_valid(
                previous_entries.get(code), raw_path, indicator_code=code, url=url
            )
            if cached:
                prior = previous_entries[code]
                payload = json.loads(raw_path.read_text(encoding="utf-8"))
                metadata, records = validate_payload(payload, code)
                collected_at = prior.get("collected_at", "")
                status = "CACHED"
            else:
                response = request_with_retries(active_client, url)
                metadata, records = decode_response(response, code)
                atomic_bytes(raw_path, response.content)
                collected_at = utc_now()
                status = "DOWNLOADED"
            entries.append(
                {
                    "indicator_code": code,
                    "indicator": definition["indicator"],
                    "official_name": definition["official_name"],
                    "unit": definition["unit"],
                    "country": COUNTRY,
                    "start_year": start_year,
                    "end_year": end_year,
                    "url": url,
                    "collected_at": collected_at,
                    "checked_at": utc_now(),
                    "status": status,
                    "raw_file": str(raw_path.resolve()),
                    "sha256": sha256_file(raw_path),
                    "size_bytes": raw_path.stat().st_size,
                    "observation_count": len(records),
                    "api_pages": int(metadata["pages"]),
                    "api_total": int(metadata["total"]),
                    "api_source_id": str(metadata.get("sourceid", "")),
                    "api_last_updated": metadata.get("lastupdated"),
                }
            )
    finally:
        if owns_client:
            active_client.close()

    statuses = {entry["status"] for entry in entries}
    manifest = {
        "schema_version": COLLECTOR_SCHEMA_VERSION,
        "publisher": "World Bank",
        "api_base_url": BASE_URL,
        "country": COUNTRY,
        "requested_period": {"start_year": start_year, "end_year": end_year},
        "checked_at": utc_now(),
        "status": statuses.pop() if len(statuses) == 1 else "MIXED",
        "indicators": entries,
    }
    atomic_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--start-year", type=int, default=START_YEAR)
    parser.add_argument("--end-year", type=int, default=END_YEAR)
    args = parser.parse_args()
    result = collect_worldbank(
        output_dir=args.output_dir,
        refresh=args.refresh,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
