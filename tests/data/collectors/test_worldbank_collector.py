import json
from pathlib import Path

import httpx
import pytest

from moroccan_markets_ai.data.collectors import worldbank_collector
from moroccan_markets_ai.data.collectors.worldbank_collector import (
    INDICATORS,
    WorldBankAPIError,
    collect_worldbank,
    fetch_indicator,
)


def api_payload(code: str) -> list:
    records = [
        {
            "indicator": {"id": code, "value": INDICATORS[code]["official_name"]},
            "country": {"id": "MA", "value": "Morocco"},
            "countryiso3code": "MAR",
            "date": str(year),
            "value": float(year - 2020),
            "unit": "",
        }
        for year in (2025, 2024, 2023)
    ]
    return [
        {
            "page": 1,
            "pages": 1,
            "per_page": 1000,
            "total": len(records),
            "sourceid": "2",
            "lastupdated": "2026-10-08",
        },
        records,
    ]


def test_collect_downloads_exact_payload_then_uses_cache(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        code = request.url.path.split("/indicator/")[1]
        return httpx.Response(200, json=api_payload(code))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = collect_worldbank(
            output_dir=tmp_path,
            client=client,
            start_year=2023,
            end_year=2025,
        )
        second = collect_worldbank(
            output_dir=tmp_path,
            client=client,
            start_year=2023,
            end_year=2025,
        )

    assert first["status"] == "DOWNLOADED"
    assert second["status"] == "CACHED"
    assert len(calls) == 2
    assert {entry["indicator_code"] for entry in second["indicators"]} == set(
        INDICATORS
    )
    for entry in second["indicators"]:
        payload = json.loads(Path(entry["raw_file"]).read_text())
        assert payload[0]["lastupdated"] == "2026-10-08"
        assert entry["observation_count"] == 3
        assert len(entry["sha256"]) == 64


def test_fetch_retries_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    code = "NY.GDP.MKTP.KD.ZG"

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="temporary")
        return httpx.Response(200, json=api_payload(code))

    monkeypatch.setattr(worldbank_collector.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = fetch_indicator(code, client=client, start_year=2023, end_year=2025)

    assert calls == 2
    assert len(records) == 3


def test_fetch_rejects_unexpected_payload() -> None:
    code = "FP.CPI.TOTL.ZG"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "world-bank-series"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(WorldBankAPIError, match="Structure"),
    ):
        fetch_indicator(code, client=client)


def test_fetch_rejects_non_retryable_http_error() -> None:
    code = "FP.CPI.TOTL.ZG"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(WorldBankAPIError, match="HTTP 404"),
    ):
        fetch_indicator(code, client=client)
