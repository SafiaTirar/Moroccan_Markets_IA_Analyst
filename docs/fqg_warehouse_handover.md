# Rapport de passation — FQG vers DuckDB

## Résumé

Le pipeline transforme les candidats financiers AMMC en une base DuckDB
auditable. Il n'admet que les décisions humaines `VERIFIED`, normalise les
unités en MAD, élimine les doublons déjà présents et reconstruit la base de
façon transactionnelle.

État validé au 19 novembre 2026 :

- 329 candidats reçus par le FQG ;
- 13 candidats `VERIFIED` ;
- 2 valeurs déjà présentes, écartées sans perte ;
- 11 nouvelles observations ;
- 36 observations financières dans la base finale ;
- 30 observations courantes et 6 comparatives ;
- 9 entreprises avec au moins une observation financière vérifiée ;
- 348 observations HCP et 32 observations World Bank ;
- 0 doublon métier, 0 ligne non vérifiée et 0 ligne en quarantaine ;
- 8 requêtes analytiques exécutées avec succès.

## Commande unique

Depuis la racine du dépôt :

```bash
uv sync --locked
uv run fqg-warehouse
```

La deuxième ligne exécute automatiquement :

1. le Financial Quality Gate sur les 329 candidats ;
2. l'export des seuls candidats `VERIFIED` ;
3. la conversion `MAD` / `KMAD` / `MMAD` en montants MAD ;
4. la normalisation des périmètres `SOCIAL` / `CONSOLIDATED` ;
5. la déduplication contre le socle warehouse ;
6. la fusion des observations et des références ;
7. la construction transactionnelle de DuckDB ;
8. la réexécution des requêtes analytiques et la validation finale.

## Sécurité

La base cible est construite dans un fichier temporaire. Elle ne remplace la
base précédente qu'après réussite de la transaction. Une preuve incomplète,
une valeur contradictoire, un statut autre que `VERIFIED`, une mauvaise unité,
une provenance absente ou un doublon métier arrête le pipeline.

La commande peut être relancée : les doublons identiques sont ignorés et la
base candidate est reconstruite de manière déterministe à partir des entrées.

## Principaux fichiers produits

| Fichier | Rôle |
|---|---|
| `data/warehouse/moroccan_markets_fqg.duckdb` | base analytique finale |
| `data/warehouse/fqg_pipeline_report.json` | résultat global des cinq étapes |
| `data/warehouse/fqg_ingestion_report.json` | audit transactionnel de l'ingestion |
| `data/warehouse/fqg_validation_report.json` | contrôles finaux de qualité |
| `data/warehouse/fqg_example_query_results.json` | résultats des requêtes de référence |

Les PDF, les bases `*.duckdb` et les intermédiaires sous `data/quality/` restent
hors Git. Le code, les tests, cette passation et les petits rapports JSON sont
versionnés.

## Options utiles

Pour tester sans toucher à la base par défaut :

```bash
uv run fqg-warehouse \
  --database /tmp/moroccan_markets_fqg_test.duckdb \
  --run-root /tmp/fqg_pipeline_test \
  --pipeline-report /tmp/fqg_pipeline_report.json
```

Le fichier de décisions humaines peut aussi être précisé :

```bash
uv run fqg-warehouse --human-decisions chemin/vers/human_decisions.json
```

## Critère de réussite

La commande doit se terminer avec `status: PASSED`. En cas d'échec, ne pas
publier la base et consulter le message d'erreur ainsi que
`data/warehouse/fqg_last_failed_ingestion.json` lorsqu'il existe.
