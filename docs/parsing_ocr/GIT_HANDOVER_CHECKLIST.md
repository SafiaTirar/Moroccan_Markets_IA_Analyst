# Checklist GitHub — Parsing & OCR

## État Git observé

- branche courante : `main` ;
- remote : `origin` → `https://github.com/SafiaTirar/Moroccan_Markets_IA_Analyst.git` ;
- worktree très sale : code Parsing/OCR, macroéconomie, warehouse, configurations, données et documentation sont mélangés ;
- aucun commit ni push n'a été exécuté pendant cette mission.

## À versionner pour Parsing & OCR

Après revue du diff, inclure au minimum :

```text
src/moroccan_markets_ai/data/collectors/ammc_corpus.py
src/moroccan_markets_ai/data/parsers/pdf_parser.py
src/moroccan_markets_ai/data/parsers/parser_validator.py
src/moroccan_markets_ai/data/parsers/ocr_recovery.py
src/moroccan_markets_ai/data/parsers/document_integrator.py
src/moroccan_markets_ai/data/parsers/financial_quality_gate.py
src/moroccan_markets_ai/data/processing/corpus_pdf_audit.py
src/moroccan_markets_ai/data/processing/corpus_pipeline_pilot.py
src/moroccan_markets_ai/data/processing/corpus_batch_runner.py
src/moroccan_markets_ai/data/processing/corpus_extension_runner.py
src/moroccan_markets_ai/data/processing/corpus_extension_14_runner.py
src/moroccan_markets_ai/data/processing/corpus_extension_15_runner.py
src/moroccan_markets_ai/data/processing/corpus_extension_16_runner.py
src/moroccan_markets_ai/data/processing/corpus_extension_17_runner.py
src/moroccan_markets_ai/data/processing/data_04_12_report.py ... data_04_17_report.py
src/moroccan_markets_ai/data/processing/financial_reference_validation.py
src/moroccan_markets_ai/data/processing/financial_validation_extension.py
src/moroccan_markets_ai/data/processing/financial_multisector_report.py
config/data/ammc_corpus.json
config/quality/ammc/
tests/data/collectors/test_ammc_corpus.py
tests/data/parsers/
tests/data/processing/test_corpus*.py
tests/data/processing/test_data_04_*.py
tests/data/processing/test_financial*.py
docs/parsing_ocr/
```

`pyproject.toml` et `uv.lock` contiennent des dépendances partagées avec DuckDB, macro et futures briques LLM. Ils sont nécessaires à la reproductibilité, mais doivent faire l'objet d'un commit de dépendances concerté, pas être attribués aveuglément à cette seule branche.

## À ne pas versionner dans Git classique

- `.env`, secrets et credentials ;
- `.venv/`, caches Python/Pytest/Ruff, `.DS_Store` ;
- les 625 MiB de `data/raw/`, notamment les PDF ;
- les 408 MiB de `data/extracted/` ;
- les artefacts complets `data/integrated/` et les 381 MiB de `data/quality/` ;
- `data/warehouse/*.duckdb` ;
- fichiers temporaires et logs.

Les manifestes, petits rapports de synthèse et références financières peuvent être publiés seulement après décision d'équipe sur la politique de données. Plusieurs JSON locaux contiennent des chemins absolus `/Users/...` : préférer des résumés portables ou un stockage d'artefacts externe.

## Lacune `.gitignore` constatée

`data/raw/*` et `data/processed/*` sont ignorés, mais `data/extracted/`, `data/integrated/`, `data/quality/` et les fichiers DuckDB ne le sont pas. Proposition à faire relire avant application :

```gitignore
data/extracted/
data/integrated/
data/quality/
data/warehouse/*.duckdb
```

Si l'équipe souhaite suivre quelques rapports de synthèse, ajouter ensuite des exceptions explicites, fichier par fichier, ou utiliser Git LFS / un stockage d'artefacts. Ne pas exempter récursivement tout `data/quality/`.

## Commandes recommandées

Ne les exécuter qu'après sauvegarde du travail concurrent et accord de l'équipe :

```bash
git status --short
git switch -c feature/data-04-parsing-ocr-handover

git add docs/parsing_ocr
git add config/data/ammc_corpus.json config/quality/ammc
git add src/moroccan_markets_ai/data/collectors/ammc_corpus.py
git add src/moroccan_markets_ai/data/parsers
git add src/moroccan_markets_ai/data/processing/corpus_pdf_audit.py
git add src/moroccan_markets_ai/data/processing/corpus_pipeline_pilot.py
git add src/moroccan_markets_ai/data/processing/corpus_batch_runner.py
git add src/moroccan_markets_ai/data/processing/corpus_extension_runner.py
git add src/moroccan_markets_ai/data/processing/corpus_extension_14_runner.py
git add src/moroccan_markets_ai/data/processing/corpus_extension_15_runner.py
git add src/moroccan_markets_ai/data/processing/corpus_extension_16_runner.py
git add src/moroccan_markets_ai/data/processing/corpus_extension_17_runner.py
git add src/moroccan_markets_ai/data/processing/data_04_12_report.py
git add src/moroccan_markets_ai/data/processing/data_04_13_report.py
git add src/moroccan_markets_ai/data/processing/data_04_14_report.py
git add src/moroccan_markets_ai/data/processing/data_04_15_report.py
git add src/moroccan_markets_ai/data/processing/data_04_16_report.py
git add src/moroccan_markets_ai/data/processing/data_04_17_report.py
git add src/moroccan_markets_ai/data/processing/financial_multisector_report.py
git add src/moroccan_markets_ai/data/processing/financial_reference_validation.py
git add src/moroccan_markets_ai/data/processing/financial_validation_extension.py
git add tests/data/collectors/test_ammc_corpus.py
git add tests/data/parsers/test_document_integrator.py
git add tests/data/parsers/test_financial_quality_gate.py
git add tests/data/parsers/test_financial_quality_regression.py
git add tests/data/parsers/test_ocr_recovery.py
git add tests/data/parsers/test_parser_validator.py
git add tests/data/processing/test_corpus_batch_runner.py
git add tests/data/processing/test_corpus_extension_runner.py
git add tests/data/processing/test_corpus_extension_14_runner.py
git add tests/data/processing/test_corpus_extension_15_runner.py
git add tests/data/processing/test_corpus_extension_16_runner.py
git add tests/data/processing/test_corpus_extension_17_runner.py
git add tests/data/processing/test_corpus_pdf_audit.py
git add tests/data/processing/test_corpus_pipeline_pilot.py
git add tests/data/processing/test_data_04_12_report.py
git add tests/data/processing/test_data_04_13_report.py
git add tests/data/processing/test_data_04_14_report.py
git add tests/data/processing/test_data_04_15_report.py
git add tests/data/processing/test_data_04_16_report.py
git add tests/data/processing/test_data_04_17_report.py
git add tests/data/processing/test_financial_multisector_report.py
git add tests/data/processing/test_financial_reference_validation.py
git add tests/data/processing/test_financial_validation_extension.py

git diff --cached --stat
git diff --cached --check
git diff --cached --name-only
uv run pytest -q
uv run ruff check src/ tests/
```

Vérifier que la liste indexée ne contient ni HCP, World Bank, `data/warehouse`, Streamlit, PDF ou `.env`. Ajouter `pyproject.toml` et `uv.lock` uniquement après arbitrage des dépendances.

Message proposé :

```text
feat(data): hand over AMMC parsing, OCR and quality pipeline
```

Puis, après accord explicite :

```bash
git commit -m "feat(data): hand over AMMC parsing, OCR and quality pipeline"
git push -u origin feature/data-04-parsing-ocr-handover
```

Créer une Pull Request vers `main`, décrire les artefacts non publiés et demander une revue séparée aux responsables Data Engineering et application. Ne pas rebaser, nettoyer ou réinitialiser le worktree partagé sans coordination.
