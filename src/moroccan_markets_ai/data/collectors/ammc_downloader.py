
import csv
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]

AMMC_DIR = PROJECT_ROOT / "data" / "raw" / "ammc"
PDF_DIR = AMMC_DIR / "pdfs"

INPUT_FILE = AMMC_DIR / "ammc_reports_metadata.csv"
MANIFEST_FILE = AMMC_DIR / "manifest.csv"

DELAY_SECONDS = 3
MAX_REPORTS = 5

MANIFEST_COLUMNS = [
    "report_title",
    "source_url",
    "pdf_url",
    "local_path",
    "downloaded_at",
    "sha256",
    "size_bytes",
    "status",
    "error",
]


def calculate_sha256(file_path: Path) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier."""

    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            sha256.update(chunk)

    return sha256.hexdigest()


def is_pdf(file_path: Path) -> bool:
    """Vérifie la signature PDF du fichier."""

    with file_path.open("rb") as file:
        return file.read(5) == b"%PDF-"


def download_pdf(
    client: httpx.Client,
    url: str,
    destination: Path,
) -> str:
    """Télécharge un PDF avec écriture temporaire."""

    if destination.exists():
        if is_pdf(destination):
            return "CACHED"
        raise ValueError(
            f"Fichier existant invalide : {destination}"
        )

    temporary = destination.with_suffix(".pdf.part")

    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()

            with temporary.open("wb") as file:
                for chunk in response.iter_bytes(
                    chunk_size=1024 * 1024
                ):
                    file.write(chunk)

        if not is_pdf(temporary):
            raise ValueError(
                "Le contenu téléchargé n'est pas un PDF"
            )

        temporary.replace(destination)

        return "DOWNLOADED"

    finally:
        temporary.unlink(missing_ok=True)


def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(INPUT_FILE)

    df = df.loc[
        (df["status"] == "FOUND")
        & df["pdf_url"].notna()
    ]

    df = df.drop_duplicates(subset=["pdf_url"])
    df = df.head(MAX_REPORTS)

    manifest = []

    headers = {
        "User-Agent": (
            "MoroccanMarketsAIAnalyst/0.1 "
            "(academic research)"
        )
    }

    with httpx.Client(
        timeout=httpx.Timeout(60, connect=10),
        follow_redirects=True,
        headers=headers,
    ) as client:

        for row in df.itertuples(index=False):
            url = row.pdf_url

            # Conserver le nom fourni par l'URL AMMC.
            filename = unquote(
                Path(urlparse(url).path).name
            )

            if not filename.lower().endswith(".pdf"):
                print(f"URL non PDF ignorée : {url}")
                continue

            # Empêche tout chemin relatif malveillant.
            filename = Path(filename).name
            destination = PDF_DIR / filename

            entry = {
                "report_title": row.report_title,
                "source_url": row.source_url,
                "pdf_url": url,
                "local_path": str(
                    destination.relative_to(PROJECT_ROOT)
                ),
                "downloaded_at": "",
                "sha256": "",
                "size_bytes": 0,
                "status": "",
                "error": "",
            }

            print(f"\nTéléchargement : {filename}")

            try:
                status = download_pdf(
                    client,
                    url,
                    destination,
                )

                entry["status"] = status
                entry["sha256"] = calculate_sha256(
                    destination
                )
                entry["size_bytes"] = (
                    destination.stat().st_size
                )

                entry["downloaded_at"] = (
                    datetime.now(UTC).isoformat()
                    if status == "DOWNLOADED"
                    else ""
                )

                print(
                    f"Statut : {status} | "
                    f"Taille : {entry['size_bytes']} octets"
                )

            except (
                httpx.HTTPError,
                OSError,
                ValueError,
            ) as exc:
                entry["status"] = "FAILED"
                entry["error"] = str(exc)
                print(f"Erreur : {exc}")

            manifest.append(entry)

            if entry["status"] != "CACHED":
                time.sleep(DELAY_SECONDS)

    with MANIFEST_FILE.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=MANIFEST_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(manifest)

    print("\n--- BILAN ---")

    result = pd.DataFrame(manifest)

    if not result.empty:
        print(result["status"].value_counts())

    print(f"\nManifest : {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
