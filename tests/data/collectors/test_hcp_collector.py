import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook

from moroccan_markets_ai.data.collectors.hcp_collector import (
    SOURCE_PAGE_URL,
    collect_hcp_ipc,
    discover_export_url,
    is_valid_xlsx,
)


def workbook_bytes() -> bytes:
    workbook = Workbook()
    workbook.active["A1"] = "test"
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def test_discover_export_url_normalizes_format_and_keeps_gid() -> None:
    html = """
    <a href="https://docs.google.com/spreadsheets/d/official-id/export?gid=42">
      le fichier de données
    </a>
    """

    assert discover_export_url(html) == (
        "https://docs.google.com/spreadsheets/d/official-id/"
        "export?format=xlsx&gid=42"
    )


def test_discover_export_url_rejects_missing_official_export() -> None:
    with pytest.raises(ValueError, match="absent"):
        discover_export_url('<a href="https://example.test/data.xlsx">data</a>')


def test_collect_downloads_then_uses_cache(tmp_path: Path) -> None:
    payload = workbook_bytes()
    calls = {"page": 0, "export": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SOURCE_PAGE_URL:
            calls["page"] += 1
            return httpx.Response(
                200,
                text=(
                    '<a href="https://docs.google.com/spreadsheets/d/'
                    'official-id/export?gid=0">fichier</a>'
                ),
            )
        calls["export"] += 1
        return httpx.Response(
            200,
            content=payload,
            headers={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                )
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        first = collect_hcp_ipc(output_dir=tmp_path, client=client)
        second = collect_hcp_ipc(output_dir=tmp_path, client=client)

    assert first["status"] == "DOWNLOADED"
    assert second["status"] == "CACHED"
    assert calls == {"page": 2, "export": 1}
    assert is_valid_xlsx(Path(first["raw_file"]))
    saved = json.loads((tmp_path / "metadata.json").read_text())
    assert saved["sha256"] == first["sha256"]
    assert saved["authenticity"]["export_discovered_on_source_page"] is True
