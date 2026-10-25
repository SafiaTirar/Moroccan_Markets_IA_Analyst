from pathlib import Path

import httpx

from moroccan_markets_ai.data.collectors.ammc_corpus import (
    ajax_fragment,
    annual_candidate_urls,
    dated_candidate_urls,
    download_with_resume,
    metadata_validation,
    parse_detail_page,
    parse_listing_fragment,
    parse_year_terms,
    report_family,
)


def test_parse_year_terms_uses_taxonomy_identifier() -> None:
    html = """
    <select name="field_annee_value_1">
      <option value="All">- Tout -</option>
      <option value="14">2023</option>
      <option value="15">2024</option>
      <option value="16">2025</option>
    </select>
    """

    assert parse_year_terms(html) == {2023: "14", 2024: "15", 2025: "16"}


def test_parse_listing_distinguishes_annual_and_semester() -> None:
    html = """
    <table><tbody>
      <tr>
        <td class="views-field-field-emetteur">
          <a href="/fr/espace-emetteurs/acme">ACME SA</a>
        </td>
        <td class="views-field-field-annee"><time>2024</time></td>
        <td class="views-field-field-type-rapp-ef-em">
          <a href="/fr/espace-emetteurs/etats-financiers/acme-rfa-2024">
            Rapports annuels
          </a>
        </td>
      </tr>
    </tbody></table>
    <nav class="pager"><a href="?page=3">Dernière page</a></nav>
    """

    rows, max_page = parse_listing_fragment(
        html,
        page=0,
        collected_at="2026-01-01T00:00:00+00:00",
    )

    assert max_page == 3
    assert rows == [
        {
            "issuer": "ACME SA",
            "exercise_year": 2024,
            "report_type": "Rapports annuels",
            "report_family": "ANNUAL",
            "detail_url": (
                "https://www.ammc.ma/fr/espace-emetteurs/"
                "etats-financiers/acme-rfa-2024"
            ),
            "listing_page": 0,
            "discovery_method": "CURRENT_AJAX_LISTING",
            "discovered_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    assert report_family("Rapports 1er semestre") == "SEMIANNUAL"


def test_parse_detail_reads_official_fields_and_all_attachments() -> None:
    html = """
    <h1>ACME - RFA 2024</h1>
    <table>
      <tr><td>Emetteur</td><td>ACME SA</td></tr>
      <tr><td>Année</td><td>2024</td></tr>
      <tr><td>Rapports financiers</td><td>Rapports annuels</td></tr>
      <tr><td>Pièce jointe</td><td>
        <a href="/sites/default/files/acme_2024.pdf">PDF</a>
      </td></tr>
    </table>
    """

    result = parse_detail_page(html, "https://www.ammc.ma/detail")

    assert result["detail_issuer"] == "ACME SA"
    assert result["detail_year"] == 2024
    assert result["detail_report_type"] == "Rapports annuels"
    assert result["pdf_urls"] == [
        "https://www.ammc.ma/sites/default/files/acme_2024.pdf"
    ]


def test_metadata_validation_flags_conflicting_exercise() -> None:
    listing = {
        "issuer": "ACME SA",
        "exercise_year": 2024,
        "report_family": "ANNUAL",
    }
    detail = {
        "detail_issuer": "ACME",
        "detail_year": 2024,
        "detail_report_type": "Rapports annuels",
    }

    status, issues = metadata_validation(
        listing,
        detail,
        "https://www.ammc.ma/sites/default/files/acme_RFA_2023.pdf",
    )

    assert status == "MISMATCH"
    assert issues == ["PDF_FILENAME_YEAR_MISMATCH"]


def test_download_resumes_partial_pdf(tmp_path: Path) -> None:
    destination = tmp_path / "report.pdf"
    partial = destination.with_suffix(".pdf.part")
    partial.write_bytes(b"%PDF-part-one")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == f"bytes={partial.stat().st_size}-"
        return httpx.Response(206, content=b"-part-two")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        status = download_with_resume(client, "https://example.test/report.pdf", destination)

    assert status == "RESUMED"
    assert destination.read_bytes() == b"%PDF-part-one-part-two"
    assert not partial.exists()


def test_ajax_fragment_requires_insert_command() -> None:
    assert ajax_fragment([{"command": "insert", "data": "<table />"}]) == (
        "<table />"
    )


def test_annual_candidate_urls_only_change_year_in_rfa_slug() -> None:
    assert annual_candidate_urls(
        "https://www.ammc.ma/fr/espace-emetteurs/etats-financiers/acme-rfa-2025",
        2023,
    ) == [
        "https://www.ammc.ma/fr/espace-emetteurs/etats-financiers/acme-rfa-2023",
        "https://v8.ammc.ma/fr/espace-emetteurs/etats-financiers/acme-rfa-2023",
    ]
    assert annual_candidate_urls("https://www.ammc.ma/unrelated", 2023) == []


def test_dated_candidate_urls_preserves_semester_slug() -> None:
    assert dated_candidate_urls(
        "https://www.ammc.ma/fr/etats-financiers/acme-rfs-juin-2024",
        2025,
    )[0] == "https://www.ammc.ma/fr/etats-financiers/acme-rfs-juin-2025"
