# Financial Quality Gate — mode d'emploi (candidats AMMC)

Ce guide explique comment contrôler les candidats financiers produits par
l'Extraction. Le contrat de données (formats, statuts, règles) est dans
[`fqg_ammc_integration_contract.md`](fqg_ammc_integration_contract.md).

## 1. Ce que fait le FQG

```text
candidats_fqg.json ──► chargement Pydantic ──► contrôles financiers ──► décisions humaines ──► rapport
   (Extraction)          (jour 2)                 (jour 3)                  (jour 4)            (jour 5)
```

| Module | Rôle |
|---|---|
| `data/parsers/fqg_handoff_loader.py` | lit et valide chaque candidat ; aucune perte silencieuse |
| `data/parsers/fqg_candidate_checks.py` | doublons, conflits, actif/passif, libellés, unités, anomalies |
| `data/parsers/fqg_source_verification.py` | échantillon, contrôles PDF/CSV/`unified_document.json`, décisions humaines |
| `data/parsers/fqg_pipeline.py` | enchaîne tout et écrit le rapport |
| `data/parsers/financial_quality_gate.py` | point d'entrée ; l'option `--candidates` active ce mode |

**Le code ne met jamais un candidat en `VERIFIED`.** Seule une décision humaine
avec preuve complète (page du PDF, valeur lue, signe, unité, périmètre, colonne,
SHA-256 du PDF, vérificateur, date) peut le faire.

## 2. Installation

```bash
uv sync --locked
```

Sous Windows, activer l'UTF-8 dans chaque terminal :

```powershell
$env:PYTHONUTF8 = "1"
```

## 3. Commande unique

```bash
uv run python -m moroccan_markets_ai.data.parsers.financial_quality_gate \
  --candidates data/quality/ammc/handoff_fqg_45/candidats_fqg.json \
  --anomalies data/quality/ammc/handoff_fqg_45/anomalies.json \
  --output data/quality/ammc/fqg_validation
```

Options : `--bilan` (par défaut `bilan.json` à côté des candidats),
`--human-decisions` (par défaut `<output>/human_decisions.json`),
`--pdf-sources` (par défaut `config/quality/ammc/fqg_pdf_sources.json`).

Sans `--candidates`, la commande garde son ancien comportement
(`... financial_quality_gate <unified_document.json>`).

### Fichiers produits dans `--output`

| Fichier | Contenu |
|---|---|
| `decisions.jsonl` | une ligne par candidat reçu, avec statut, raisons et provenance |
| `admissible_values.jsonl` | **uniquement** les candidats `VERIFIED` (seule entrée autorisée en aval) |
| `verified_sample.json` | échantillon : décisions `VERIFIED` / `PDF_CONFIRMED` avec preuve |
| `rejected_sample.json` | échantillon : décisions `REJECTED` avec raison |
| `review_required.json` | échantillon : candidats encore à revoir |
| `refused_human_decisions.json` | décisions humaines refusées (preuve incomplète) |
| `fqg_report.json` / `fqg_report.md` | rapport récapitulatif |

## 4. Vérifier l'échantillon contre les sources (jour 4)

Les sources sont dans le paquet de passation du Parsing
(`AMMC_annual_45.zip`), à extraire **hors Git** :

```text
data/raw/ammc/AMMC_annual_45/<entreprise>/<année>/
  original/<rapport>.pdf
  tables/table_NNN.csv
  unified_document.json
```

1. Registre des PDF et de leur SHA-256 (depuis les `ocr_metadata.json`) :
   ```bash
   uv run python -m moroccan_markets_ai.data.parsers.fqg_source_verification sources
   ```
2. Échantillon (32 candidats, 11 entreprises, 2023-2025) et grille :
   ```bash
   uv run python -m moroccan_markets_ai.data.parsers.fqg_source_verification worksheet
   ```
   Crée `verification_worksheet.json` (contrôles automatiques et proposition)
   et `human_decisions.json` (jamais écrasé s'il existe déjà).
3. Pour chaque candidat : ouvrir le PDF à la page indiquée, puis remplir
   `decision`, `reason`, `checked_by`, `checked_at`, `evidence.value_read`,
   `evidence.sign` et corriger `unit` / `scope` / `column_year` si besoin.
4. Appliquer et produire les livrables :
   ```bash
   uv run python -m moroccan_markets_ai.data.parsers.fqg_source_verification apply
   ```

| Décision | Preuve exigée |
|---|---|
| `VERIFIED` | page, valeur lue = valeur extraite, signe, unité, périmètre, colonne = année observée, SHA-256 du PDF |
| `PDF_CONFIRMED` | page, valeur lue = valeur extraite, signe |
| `REJECTED` | raison (indiquer la bonne valeur lue dans le PDF) |
| `REVIEW_REQUIRED` | raison |

Une décision incomplète est refusée et listée dans
`refused_human_decisions.json` ; le candidat garde alors son statut.

## 5. Tests et qualité

```bash
uv run pytest tests/data/parsers -q -k fqg
uv run ruff check src/moroccan_markets_ai/data/parsers tests/data/parsers
```

## 6. Ce qui va dans Git

- Oui : code, tests, `docs/`, `config/quality/ammc/fqg_pdf_sources.json`,
  `data/quality/ammc/handoff_fqg_45/` et les livrables de
  `data/quality/ammc/fqg_validation/` (petits JSON). `data/quality/` étant
  ignoré par `.gitignore`, les ajouter avec `git add -f`.
- Non : PDF, `data/raw/`, `data/extracted/`, `data/integrated/`.
