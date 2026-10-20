
import csv
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.ammc.ma"

LIST_URL = (
    f"{BASE_URL}/fr/liste-etats-financiers-emetteurs"
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]

OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "ammc"

DELAY_SECONDS = 3


def fetch_page(client: httpx.Client, page: int) -> str:
    """Télécharge une page HTML de la liste AMMC."""

    print(f"Consultation de la page {page}...")

    response = client.get(
        LIST_URL,
        params={"page": page},
    )

    response.raise_for_status()

    return response.text


def discover_links(html: str, page: int) -> list[dict]:
    """Identifie les liens candidats vers des rapports."""

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        url = urljoin(BASE_URL, href)
        label = anchor.get_text(" ", strip=True)

        parsed = urlparse(url)

        # Ne garder que les liens du domaine AMMC.
        if parsed.hostname not in {
            "ammc.ma",
            "www.ammc.ma",
        }:
            continue

        path = parsed.path.lower()

        if (
            "/etats-financiers/" not in path
            and not path.endswith(".pdf")
        ):
            continue

        results.append({
            "listing_page": page,
            "label": label,
            "url": url,
            "collected_at": datetime.now(
                UTC
            ).isoformat(),
        })

    return results


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_links = []

    headers = {
        "User-Agent": "MoroccanMarketsAIAnalyst/0.1 "
        "(academic research; contact: project maintainer)"
    }

    with httpx.Client(
        timeout=45,
        follow_redirects=True,
        headers=headers,
    ) as client:

        for page in range(3):
            try:
                html = fetch_page(client, page)

                # Garder une copie pour inspecter le HTML.
                html_path = OUTPUT_DIR / f"listing_{page}.html"
                html_path.write_text(
                    html,
                    encoding="utf-8",
                )

                links = discover_links(html, page)

                print(
                    f"Page {page} : "
                    f"{len(links)} liens candidats"
                )

                all_links.extend(links)

            except httpx.HTTPError as exc:
                print(f"Erreur page {page}: {exc}")

            time.sleep(DELAY_SECONDS)

    # Dédupliquer selon l'URL
    unique_links = {
        item["url"]: item
        for item in all_links
    }

    output_file = OUTPUT_DIR / "ammc_inventory.csv"

    with output_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "listing_page",
                "label",
                "url",
                "collected_at",
            ],
        )

        writer.writeheader()
        writer.writerows(unique_links.values())

    print("\nInventaire terminé.")
    print(f"Liens candidats : {len(unique_links)}")
    print(f"Fichier : {output_file}")


if __name__ == "__main__":
    main()
