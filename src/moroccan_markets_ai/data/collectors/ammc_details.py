
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pandas as pd
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "ammc"

INPUT_FILE = RAW_DIR / "ammc_inventory.csv"
OUTPUT_FILE = RAW_DIR / "ammc_reports_metadata.csv"

BASE_URL = "https://www.ammc.ma"


def extract_report_details(html: str, source_url: str) -> dict:
    """Extrait les liens PDF et le titre d'une fiche AMMC."""

    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1")

    report_title = (
        title.get_text(" ", strip=True)
        if title
        else None
    )

    pdf_links = []

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        pdf_url = urljoin(BASE_URL, href)

        if pdf_url.lower().split("?")[0].endswith(".pdf"):
            pdf_links.append(pdf_url)

    pdf_links = list(dict.fromkeys(pdf_links))

    return {
        "report_title": report_title,
        "source_url": source_url,
        "pdf_count": len(pdf_links),
        "pdf_url": pdf_links[0] if len(pdf_links) == 1 else None,
        "status": (
            "FOUND" if len(pdf_links) == 1
            else "REVIEW_REQUIRED"
        ),
    }


def main():
    df = pd.read_csv(INPUT_FILE)

    # Premier test : cinq fiches seulement.
    urls = df["url"].dropna().drop_duplicates().head(5)

    results = []

    with httpx.Client(
        timeout=45,
        follow_redirects=True,
        headers={
            "User-Agent": "MoroccanMarketsAIAnalyst/0.1"
        },
    ) as client:

        for url in urls:
            print(f"\nAnalyse : {url}")

            try:
                response = client.get(url)
                response.raise_for_status()

                details = extract_report_details(
                    response.text,
                    url,
                )

                results.append(details)

                print("Titre :", details["report_title"])
                print("PDF :", details["pdf_url"])
                print("Statut :", details["status"])

            except httpx.HTTPError as exc:
                print("Erreur HTTP :", exc)

                results.append({
                    "report_title": None,
                    "source_url": url,
                    "pdf_count": 0,
                    "pdf_url": None,
                    "status": "HTTP_ERROR",
                })

            time.sleep(3)

    result_df = pd.DataFrame(results)

    result_df.to_csv(OUTPUT_FILE, index=False)

    print("\nRésultat :")
    print(result_df.to_string(index=False))

    print(f"\nFichier généré : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
