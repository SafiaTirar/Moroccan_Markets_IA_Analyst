
from pathlib import Path

import pandas as pd
import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[4]

PDF_DIR = PROJECT_ROOT / "data" / "raw" / "ammc" / "pdfs"

OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "raw" / "ammc" / "pdf_audit.csv"
)

KEYWORDS = [
    "chiffre d'affaires",
    "chiffre d’affaires",
    "résultat net",
    "resultat net",
    "bilan",
    "capitaux propres",
    "produits d'exploitation",
    "produit net bancaire",
]


def audit_pdf(pdf_path: Path) -> list[dict]:
    results = []

    with pymupdf.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text")
            normalized = " ".join(
                text.lower().split()
            )

            found_keywords = [
                keyword
                for keyword in KEYWORDS
                if keyword in normalized
            ]

            results.append({
                "filename": pdf_path.name,
                "page": page_number,
                "characters": len(text.strip()),
                "has_text": bool(text.strip()),
                "keywords": ", ".join(found_keywords),
                "keyword_count": len(found_keywords),
            })

    return results


def main():
    all_results = []

    for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
        print(f"Analyse : {pdf_path.name}")

        all_results.extend(
            audit_pdf(pdf_path)
        )

    if not all_results:
        print("Aucun PDF trouvé.")
        return

    df = pd.DataFrame(all_results)

    df.to_csv(OUTPUT_FILE, index=False)

    print("\n=== RÉSUMÉ ===")

    summary = df.groupby("filename").agg(
        pages=("page", "count"),
        pages_with_text=("has_text", "sum"),
        total_characters=("characters", "sum"),
        pages_with_keywords=(
            "keyword_count",
            lambda values: (values > 0).sum(),
        ),
    )

    print(summary.to_string())

    print("\n=== PAGES FINANCIÈRES CANDIDATES ===")

    candidates = df[df["keyword_count"] > 0]

    print(
        candidates[
            ["filename", "page", "characters", "keywords"]
        ].to_string(index=False)
    )

    print(f"\nAudit enregistré : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
