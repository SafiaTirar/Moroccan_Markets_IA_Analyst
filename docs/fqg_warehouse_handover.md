# Rapport de passation — FQG vers DuckDB

## Résumé

Le pipeline transforme les candidats financiers AMMC en une base DuckDB
auditable. Il n'admet que les décisions humaines `VERIFIED`, normalise les
unités en MAD, élimine les doublons déjà présents et reconstruit la base de
façon transactionnelle.

État validé au 25 novembre 2026 sur `handoff_fqg_v2_final` :

- 327 candidats reçus par le FQG ;
- 47 candidats `VERIFIED` ;
- 2 valeurs déjà présentes, écartées sans perte ;
- 45 nouvelles observations ;
- 70 observations financières dans la base finale ;
- 47 observations courantes et 23 comparatives ;
- 13 entreprises avec au moins une observation financière vérifiée ;
- 354 observations HCP et 32 observations World Bank ;
- 0 doublon métier, 0 ligne non vérifiée et 0 ligne en quarantaine ;
- 8 requêtes analytiques exécutées avec succès.

Le pipeline utilise par défaut le handoff
`data/quality/ammc/handoff_fqg_v2_final`, livré par la PR #7. Les 37 décisions
`PDF_CONFIRMED` et les 8 décisions humaines obsolètes ne sont pas admises dans
le Warehouse. Le manifeste local est reconstruit à partir des 45 empreintes
SHA-256 de `config/quality/ammc/fqg_pdf_sources.json` ; les PDF ne sont pas
retéléchargés pour cette reconstruction.

## Commande unique

Depuis la racine du dépôt :

```bash
uv sync --locked
uv run fqg-warehouse
```

La deuxième ligne exécute automatiquement :

1. le Financial Quality Gate sur les 327 candidats ;
2. l'export des seuls candidats `VERIFIED` ;
3. la conversion `MAD` / `KMAD` / `MMAD` en montants MAD ;
4. la normalisation des périmètres `SOCIAL` / `CONSOLIDATED` ;
5. la déduplication contre le socle warehouse ;
6. la fusion des observations et des références ;
7. la construction transactionnelle de DuckDB ;
8. la réexécution des requêtes analytiques et la validation finale.

Les entrées FQG peuvent être remplacées explicitement :

```bash
uv run fqg-warehouse \
  --candidates data/quality/ammc/handoff_fqg_v2_final/candidats_fqg.json \
  --anomalies data/quality/ammc/handoff_fqg_v2_final/anomalies.json \
  --bilan data/quality/ammc/handoff_fqg_v2_final/bilan.json
```

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
  --ingestion-report /tmp/fqg_ingestion_report.json \
  --failed-report /tmp/fqg_last_failed_ingestion.json \
  --query-results /tmp/fqg_example_query_results.json \
  --validation-report /tmp/fqg_validation_report.json \
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

## Prochaine étape — Phase 3 du roadmap

Le stockage validé clôt la Phase 2 « Extract and store ». La tâche suivante est
le livrable d'analyse de la semaine 5, avant le développement du chat :

1. interroger DuckDB en lecture seule et produire trois constats financiers
   reproductibles, chacun accompagné d'un graphique Plotly ;
2. comparer les entreprises et les périodes uniquement à partir des 70
   observations `VERIFIED`, en conservant les versions courantes et
   comparatives distinctes ;
3. construire des ratios documentés, standardiser les variables puis tester
   un clustering de `k = 2` à `k = 5` avec le score de silhouette ;
4. expliquer chaque groupe en langage simple et documenter les limites liées à
   la couverture encore incomplète ;
5. ajouter des tests SQL sur les chiffres utilisés dans les graphiques.

Le critère de fin de cette étape est un notebook ou rapport d'analyse contenant
trois résultats vérifiables avec graphiques et, si la couverture le permet, un
clustering expliqué. Selon le roadmap, l'exactitude des chiffres et les tests
restent prioritaires sur le clustering si le calendrier prend du retard.

La construction des trois pages Streamlit (`Overview`, `Company`, `Chat`) et de
l'agent à deux outils (`run_sql` en lecture seule et `make_chart`) vient ensuite,
pendant les semaines 5–6.
