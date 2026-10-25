# Runbook Parsing & OCR AMMC

## 1. Environnement vérifié

- Python 3.12 (`.python-version`, projet contraint à `>=3.12,<3.13`) ; environnement observé : 3.12.10.
- macOS Intel x86_64, 8 Go de RAM pour les traitements historiques.
- `uv` pour l'environnement et le lockfile.
- Docling 2.140.0, PyMuPDF 1.28.2, PyTorch 2.2.2, NumPy 1.26.4.
- Tesseract CLI 5.5.3 ; langues requises et vérifiées : `fra`, `eng`.

Installation macOS :

```bash
brew install uv tesseract tesseract-lang
uv sync --frozen
uv run python --version
tesseract --version
tesseract --list-langs | grep -E '^(fra|eng)$'
uv run python -c "import docling, pymupdf; print(docling.__version__, pymupdf.__version__)"
```

Sous Linux, installer les paquets de distribution fournissant Tesseract et les données `fra`/`eng`, puis utiliser `uv sync --frozen`. Sous Windows, installer Tesseract séparément et ajouter son répertoire au `PATH`. Ces deux plateformes n'ont pas été utilisées pour les métriques du corpus ; les noms de paquets système varient.

Aucune variable d'environnement n'est nécessaire au parsing local. Les fichiers `.env` concernent d'autres parties du projet et ne doivent jamais être publiés.

## 2. Préflight

Depuis la racine du projet :

```bash
git status --short
test -f data/raw/ammc/corpus/download_manifest.csv
uv run python -m moroccan_markets_ai.data.processing.corpus_pdf_audit --help
uv run python -m moroccan_markets_ai.data.parsers.pdf_parser --help
uv run python -m moroccan_markets_ai.data.parsers.ocr_recovery --help
```

Les fichiers source doivent correspondre au SHA-256 du manifeste. Ne déplacez pas un PDF déjà traité sans mettre à jour le workflow de provenance.

## 3. Audit PyMuPDF

Commande réelle, avec reprise par défaut :

```bash
uv run python -m moroccan_markets_ai.data.processing.corpus_pdf_audit
```

Options disponibles :

```text
--manifest PATH
--output-dir PATH
--no-resume
```

L'audit écrit `annual_pdf_audit.csv`, `annual_pdf_pages.csv`, `annual_processing_plan.csv` et `annual_pdf_audit_report.json`. Il n'exécute ni Docling ni Tesseract.

## 4. Parser un PDF isolé

```bash
uv run python -m moroccan_markets_ai.data.parsers.pdf_parser \
  data/raw/ammc/pdfs/annual/s2m/2025/S2M_RFA_2025.pdf
```

Le cache Docling est réutilisé si le PDF, la version Docling, le backend, la configuration et tous les hashes d'artefacts concordent. Il n'existe pas d'option `--force` pour ce CLI : une invalidation correcte doit venir d'un changement d'entrée/configuration, pas de la suppression manuelle improvisée du cache.

## 5. OCR ciblé

À partir des pages du validateur :

```bash
uv run python -m moroccan_markets_ai.data.parsers.ocr_recovery PDF \
  --parser-metadata data/extracted/ammc/NOM_SHA/metadata.json
```

Pages explicites :

```bash
uv run python -m moroccan_markets_ai.data.parsers.ocr_recovery PDF \
  --pages 5,6,9-10
```

Régions configurées :

```bash
uv run python -m moroccan_markets_ai.data.parsers.ocr_recovery PDF \
  --regions-json regions.json
```

`--force` existe, mais doit rester exceptionnel : il recalcule l'OCR. La configuration actuelle est `fra+eng`, PSM 6 et 11, 300 dpi, images minimales 400×250, région minimale 3 % de la page, au moins 5 montants, tolérance de colonne 0,045.

## 6. Intégration et qualité

```bash
uv run python -m moroccan_markets_ai.data.parsers.document_integrator \
  --docling data/extracted/ammc/NOM_SHA/metadata.json \
  --ocr data/extracted/ammc/ocr_generic/NOM_SHA/CONFIG/ocr_metadata.json

uv run python -m moroccan_markets_ai.data.parsers.financial_quality_gate \
  data/integrated/ammc/NOM_SHA/unified_document.json \
  --rules config/quality/ammc/REGLES.json
```

Sans fichier de règles batch strict, `QualityRules` possède un défaut plus permissif incluant `NOT_CHECKED`. Les orchestrateurs du corpus remplacent explicitement ce défaut par `downstream_allowed_statuses=("VERIFIED",)`. Pour un flux destiné à l'aval, utiliser le runner strict ou une configuration contrôlée ; ne pas appeler le Quality Gate sans comprendre cette différence.

## 7. Traitement de lot et reprise

Les anciens runners correspondent à des sélections figées. Le dernier lot peut être inspecté ou rejoué depuis ses caches avec :

```bash
uv run python -m moroccan_markets_ai.data.processing.corpus_extension_17_runner --dry-run
uv run python -m moroccan_markets_ai.data.processing.corpus_extension_17_runner --resume
```

`--no-resume` reconvertirait les documents autorisés et ne doit pas être utilisé sur le corpus complet sans justification. Les runners exécutent un seul document à la fois dans un sous-processus, enregistrent `pilot_result.json` après chaque PDF et valident les hashes avant réutilisation.

Ne relancez pas successivement les runners DATA-04.12 à 04.17 pour « reconstruire » le corpus : ils représentent des lots historiques. Utilisez le manifeste, le plan et un nouvel orchestrateur explicitement borné si le corpus évolue.

## 8. Diagnostic

- `Tesseract CLI est introuvable` : vérifier `command -v tesseract` et le `PATH`.
- langue manquante : vérifier `tesseract --list-langs`; l'OCR exige `fra` et `eng`.
- cache Docling ignoré : comparer source SHA, `docling_version`, backend et `extraction_config_sha256` dans `metadata.json`.
- cache OCR ignoré : comparer source, `config_sha256` et `selection_sha256`.
- `OCR_NO_USABLE_TABLE` : l'exécution peut être correcte sans région financière exploitable ; conserver le statut et les régions rejetées.
- `PARTIAL` : lire les issues et la quarantaine ; ce n'est pas automatiquement un crash.
- risque mémoire : ne pas paralléliser. La garde actuelle est 7 Gio, le pic observé 3,43 Gio environ.

## 9. Contrôles avant handover

```bash
uv run pytest -q
uv run ruff check src/ tests/
git diff --check
```

Dernière exécution documentaire : 152 tests réussis, Ruff réussi, `git diff --check` réussi. La mission de documentation doit relancer ces contrôles sans relancer Docling/OCR.
