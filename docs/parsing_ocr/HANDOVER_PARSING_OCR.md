# Handover technique — Parsing & OCR AMMC

## 1. Résumé exécutif

La phase Parsing & OCR transforme le corpus annuel AMMC en artefacts documentaires traçables, contrôlés et réutilisables. Elle ne réalise pas l'extraction sémantique finale des indicateurs et ne remplace pas une validation comptable.

Le problème traité est hétérogène : les rapports AMMC combinent texte natif, tableaux complexes, pages scannées, images de tableaux, colonnes comparatives et présentations sectorielles. Une stratégie unique perd soit la structure, soit les valeurs, et une OCR généralisée serait plus lente et parfois moins fiable que le texte natif.

La solution implémentée enchaîne :

1. collecte et manifeste avec SHA-256 ;
2. audit léger PyMuPDF et classification `TEXT`, `MIXED`, `IMAGE` ou `UNKNOWN` ;
3. planification séquentielle adaptée à 8 Go de RAM ;
4. Docling avec `PyPdfiumDocumentBackend` pour les documents `TEXT`/`MIXED` ;
5. OCR Tesseract ciblé, ou OCR-first pour les documents `IMAGE` ;
6. validation structurelle ;
7. intégration non destructive des tables Docling et OCR ;
8. Financial Quality Gate et validation séparée contre des références PDF fiables.

Le corpus annuel est couvert à 45/45 rapports : 15 entreprises × exercices 2023, 2024 et 2025, soit 4 875 pages. Les 45 traitements ont produit un résultat exploitable par la chaîne documentaire ; aucun PDF n'est techniquement bloqué. Tous sont néanmoins classés `PARTIAL`, car au moins une anomalie documentaire ou une ambiguïté subsiste. Ce statut ne signifie ni échec de conversion ni validation financière.

### Résultats consolidés actuels

| Mesure | Résultat issu des artefacts actuels |
|---|---:|
| Rapports présents et traités | 45/45 (100 %) |
| Pages | 4 875 |
| Classification documentaire | 31 `TEXT`, 12 `MIXED`, 2 `IMAGE` |
| Tables Docling détectées | 4 651 |
| Tables Docling non vides | 4 246 |
| Pages effectivement envoyées à l'OCR | 826 |
| Tables OCR non vides récupérées | 151 |
| Pages en échec technique final | 0 |
| Tables intégrées sans page source | 0 |
| Durée cumulée des composants mesurés | 29 983,175 s, soit 8,33 h |
| Pic RSS observé | 3 512,64 MiB |
| Statut final | 45 `PARTIAL` |

Le snapshot documentaire strict des 45 résultats contient 92 204 valeurs candidates : 0 `VERIFIED`, 24 804 `REVIEW_REQUIRED`, 2 178 `REJECTED` et 65 222 `NOT_CHECKED`. Les 89 observations `VERIFIED` mentionnées dans le rapport DATA-04.17 proviennent des validations de référence antérieures et constituent un ensemble séparé. Elles ne doivent pas être ajoutées aveuglément aux 92 204 candidates, ni être déduites de leur répartition.

### Capacités remises aux équipes suivantes

- documents Markdown et JSON issus de Docling ;
- tables CSV Docling et OCR avec page, source et coordonnées lorsque disponibles ;
- document intégré sans écrasement des artefacts originaux ;
- rapports d'anomalies et files de revue ;
- références financières révisables ;
- export admissible strictement limité aux observations `VERIFIED` dans les orchestrateurs de corpus ;
- cache vérifié par empreintes de la source, de la configuration et des artefacts.

### Vocabulaire à ne pas confondre

| Étape | Ce qu'elle garantit | Ce qu'elle ne garantit pas |
|---|---|---|
| Collecte PDF | fichier récupéré, provenance et SHA-256 | lisibilité des pages |
| Audit documentaire | nature technique, pages OCR candidates, charge | extraction des valeurs |
| Parsing Docling | texte, structure et tables candidates | exactitude comptable de chaque cellule |
| OCR Tesseract | récupération de texte sur image avec coordonnées | correction des ambiguïtés visuelles |
| Validation structurelle | artefacts lisibles, dimensions, cellules, pages | signification financière |
| Intégration | coexistence traçable des sources Docling/OCR | fusion comptable des valeurs |
| Validation financière | concordance avec une référence PDF fiable | exhaustivité du rapport |
| Qwen3, futur | extraction sémantique structurée | preuve indépendante de vérité |

## 2. Architecture réelle

Le diagramme, les seuils et la table détaillée des composants sont dans [ARCHITECTURE.md](ARCHITECTURE.md). Les modules essentiels sont sous `src/moroccan_markets_ai/data/` :

```text
collectors/
└── ammc_corpus.py
parsers/
├── pdf_parser.py
├── parser_validator.py
├── ocr_recovery.py
├── document_integrator.py
└── financial_quality_gate.py
processing/
├── corpus_pdf_audit.py
├── corpus_pipeline_pilot.py
├── corpus_batch_runner.py
├── corpus_extension_runner.py
├── corpus_extension_14_runner.py
├── corpus_extension_15_runner.py
├── corpus_extension_16_runner.py
├── corpus_extension_17_runner.py
├── financial_reference_validation.py
├── financial_validation_extension.py
├── financial_multisector_report.py
└── data_04_12_report.py ... data_04_17_report.py
```

Flux résumé : manifeste AMMC → audit PyMuPDF → plan → worker séquentiel → Docling/OCR → validation → intégration → Quality Gate → références fiables/export `VERIFIED`.

### Choix technologiques

- PyMuPDF inspecte rapidement texte, pages et images sans charger les modèles Docling.
- Docling conserve mieux la structure documentaire et les tableaux natifs.
- `PyPdfiumDocumentBackend` évite le `SIGSEGV` observé avec Stroc dans le backend précédent.
- Tesseract est appelé séparément afin de cibler les pages nécessaires, comparer PSM 6/11 et conserver les coordonnées TSV.
- Les sous-processus isolés et l'exécution strictement séquentielle réduisent les risques sur le Mac Intel 8 Go.
- Les SHA-256 et empreintes de configuration rendent le cache invalidable et auditable.

## 3. Traitement détaillé d'un PDF

### A. Vérification préalable

L'orchestrateur résout une ligne du manifeste et vérifie le chemin local, le fichier PDF, son SHA-256, l'identité société/exercice et l'absence de doublon. Il récupère la ligne d'audit existante au lieu de rouvrir inutilement tout le corpus. Le worker calcule une empreinte comprenant la source, la stratégie, le backend et les configurations d'extraction. Une reprise n'est acceptée que si les artefacts annoncés et leurs hashes restent cohérents.

### B. Classification documentaire

`corpus_pdf_audit.py` utilise les seuils configurables suivants : 40 caractères pour considérer qu'une page contient du texte, 200 caractères pour un texte substantiel et 15 % de surface pour une image significative. Une page est :

- `TEXT` si elle porte du texte sans image significative ;
- `IMAGE` si elle porte une image significative sans texte ;
- `MIXED` si les deux sont présents ;
- `UNKNOWN` si aucun des deux critères n'est atteint.

Un document est `IMAGE` si au moins 80 % de ses pages le sont. Il est `TEXT` si au moins 80 % ont du texte et au plus 20 % sont candidates OCR. Les autres documents contenant du texte ou des images sont `MIXED`; à défaut ils sont `UNKNOWN`.

Les pages `IMAGE` sont candidates OCR. Les pages `MIXED` de moins de 200 caractères le sont également. Une page `UNKNOWN` n'est retenue que si les indices d'image le justifient. Après Docling, cette sélection est complétée par les anomalies du validateur ; l'audit initial comptait 720 pages candidates, alors que l'union réellement traitée en compte 826.

### C. Parsing Docling

`AMMCPDFParser` configure Docling avec `PyPdfiumDocumentBackend`, OCR interne désactivé et structure des tableaux activée. Il exporte :

```text
document.md
document.json
tables/*.csv
metadata.json
```

Les métadonnées enregistrent notamment le PDF source, son SHA-256, la version de Docling, le backend, la configuration, le nombre de pages, les tables et leurs pages. Le cache est rejeté si la source, la version, le backend, la configuration ou le hash d'un artefact diffère. Une exception est isolée au document ; le runner conserve les traitements antérieurs et poursuit si l'échec n'est pas global.

### D. Validation structurelle

`ParserValidator` contrôle les artefacts attendus, la lecture des CSV, les dimensions, cellules vides, tables sans ligne ou colonne, pages inconnues, en-têtes générés, lignes sans libellé, mots découpés, incohérences de forme et images à caractère financier sans table exploitable.

- `PARSED` : aucune anomalie définie par le validateur ;
- `PARTIAL` : conversion exploitable mais anomalies restantes ;
- `FAILED` : document/pages indispensables absents, artefacts critiques invalides ou aucune table utilisable selon le contexte.

Les codes `EMPTY_TABLE`, `FINANCIAL_IMAGE_REQUIRES_OCR` et `IMAGE_TABLE_REQUIRES_OCR` alimentent notamment la sélection OCR. Un `FAILED` au niveau OCR signifie souvent « aucune table OCR exploitable » et non « échec du document complet » si Docling reste utilisable.

### E. OCR ciblé et OCR-first

La configuration observée utilise Tesseract `fra+eng`, PSM 6 et 11, rendu 300 DPI, taille minimale de région 400 × 250 pixels, surface minimale de région de 3 %, au moins cinq montants candidats et tolérance de regroupement en colonnes de 0,045.

`ocr_recovery.py` recherche les images intégrées, supprime les régions se recouvrant presque totalement (IoU > 0,95), peut utiliser la page entière en repli et la découper gauche/droite si l'heuristique de mise en page le demande. Il compare les essais OCR, reconstruit les lignes depuis le TSV, regroupe les valeurs par bord droit et normalise la représentation des nombres français sans inventer les caractères absents. Les coordonnées, la page, la région, le PSM et la source restent attachés au résultat.

- `DOCLING_FIRST_TARGETED_OCR` : documents `TEXT` et `MIXED`; Docling est la base et l'OCR complète les pages justifiées.
- `OCR_FIRST` : documents `IMAGE`; l'OCR est exécuté avant la tentative Docling et reste disponible si Docling ne récupère aucune table non vide.

Le cache OCR dépend du SHA-256 source, de l'empreinte de configuration et de la sélection pages/régions. Les risques connus sont la concaténation de montants voisins, les signes/parenthèses perdus, les colonnes mal regroupées et les unités absentes.

### F. Intégration non destructive

`document_integrator.py` vérifie que Docling et OCR concernent le même PDF, contrôle les hashes de leurs CSV et coordonnées, puis écrit un `unified_document.json`. Les tables Docling reçoivent des identifiants `DOCLING_nnn`; les tables OCR conservent des identifiants `OCR_...`. Les OCR strictement identiques sont dédupliquées par page et hash CSV.

Les tables sources ne sont jamais écrasées. Le champ de remplacement signale uniquement qu'une OCR est candidate pour une table Docling invalide. Les documents intégralement OCR, hybrides et sans OCR sont donc représentables avec la même structure et une provenance explicite.

## 4. Validation et sécurité financière

Le Quality Gate est une couche indépendante du parsing. Il annote chaque candidat avec l'un des statuts :

- `VERIFIED` : concordance complète avec une référence fiable issue du PDF original ;
- `REVIEW_REQUIRED` : preuve ou contexte insuffisant, ambigu ou contradictoire ;
- `REJECTED` : valeur structurellement impossible ou explicitement discordante ;
- `NOT_CHECKED` : aucun contrôle probant n'a encore été appliqué.

Une référence fiable doit permettre de contrôler identité de l'entreprise, indicateur, montant et signe, exercice/colonne, monnaie, échelle, périmètre comptable, page, table, SHA-256 du PDF et preuve textuelle ou visuelle. Une égalité actif/passif, un sous-total ou plusieurs occurrences cohérentes sont des indices complémentaires, jamais une preuve suffisante à eux seuls.

Les orchestrateurs de corpus imposent :

```python
downstream_allowed_statuses = ["VERIFIED"]
```

Attention : l'objet `QualityRules` générique possède aussi un comportement par défaut incluant `NOT_CHECKED`; il ne faut donc pas appeler naïvement le CLI autonome pour un export aval. Utiliser la configuration stricte des runners ou vérifier explicitement la règle avant toute ingestion.

Cas réellement rencontrés : 18 montants concaténés dans RCI, trois lignes sans libellé dans SRM, tables Stroc ambiguës, parenthèses/signes OCR perdus et confusion possible entre comptes sociaux/consolidés. Pour Dari Couspate 2025, la page physique 34 (numéro imprimé 35) montre un chiffre d'affaires de `818 399 882`, alors que l'OCR a produit `8 183 993 882`; cette valeur ne peut pas être promue. Pour S2M, les valeurs comparatives 2024 publiées dans le rapport 2025 sont conservées comme versions distinctes de celles du rapport 2024.

Voir [DATA_QUALITY_AND_LIMITATIONS.md](DATA_QUALITY_AND_LIMITATIONS.md) pour les règles détaillées et les risques.

## 5. Chronologie des travaux

### Pilotes initiaux — S2M, Stroc, RCI, Dari Couspate et SRM

Objectif : vérifier Docling, diagnostiquer les tables vides et introduire l'OCR ainsi qu'une validation générique. Le passage à `PyPdfiumDocumentBackend` a supprimé le crash `SIGSEGV` de Stroc. L'OCR régional et l'intégrateur ont ensuite été ajoutés pour conserver les deux sources. Les cas RCI/SRM/Stroc ont conduit à la quarantaine et au Financial Quality Gate. Il n'existe pas dans le dépôt de rapport batch figé unique regroupant tous ces tout premiers essais ; leurs artefacts unitaires et les rapports ultérieurs sont la source disponible.

### DATA-04.7 — Audit technique des 45 rapports

- objectif : inventorier le corpus sans Docling/OCR massif ;
- résultat : 45 PDF, 4 875 pages, 31 `TEXT`, 12 `MIXED`, 2 `IMAGE` ;
- pages candidates OCR à ce stade : 720 ;
- plan : 25 lots théoriques, un document à la fois ;
- apport : classification/page, indices financiers, stratégie et estimation de charge.

### DATA-04.8 — Pilotes TEXT/MIXED

- documents : S2M 2025 et Dari Couspate 2023 ;
- résultat rapporté : 70 pages et environ 765,995 s de composants mesurés dans les résultats actuels ;
- apport : orchestration séquentielle, suivi RSS, périmètre comptable et réutilisation du Quality Gate ;
- décision : `GO AVEC RÉSERVES`, car les deux documents restaient `PARTIAL` et aucune valeur n'était encore prouvée par référence indépendante.

### DATA-04.9 — Références financières

Objectif : comparer un petit échantillon directement aux PDF et démontrer qu'une valeur réellement prouvée devient admissible. Le rapport de mission historique mentionne 1 504 candidates, neuf comparaisons, quatre `VERIFIED`, quatre `REVIEW_REQUIRED` et une `REJECTED`. Le dépôt actuel ne contient pas un snapshot autonome et figé permettant de recalculer exactement ces quatre validations initiales : les configurations ont ensuite été étendues. Ces chiffres doivent donc être lus comme historique, pas comme état courant reproductible.

### DATA-04.10 — Extension ciblée

Le rapport `validation_extension/quality_gate_progress.json` porte sur 1 504 candidates, un échantillon de 13 et quatre dossiers de revue. Il enregistre neuf références validées au total, trois nouvelles par rapport à sa baseline de six, trois à revoir et une rejetée. L'étape introduit le registre d'indicateurs, les synonymes sectoriels, la file priorisée et les contrôles déterministes.

### DATA-04.11 — Généralisation multisectorielle

Le périmètre est étendu à cinq documents : S2M 2025, Dari Couspate 2023, Attijariwafa Bank 2025, Wafa Assurance 2025 et Aradei Capital 2025. Le rapport porte sur 9 101 candidates et un échantillon de 20 : 17 références fiables, 16 valeurs vérifiées, trois à revoir et une rejetée. L'étape confirme qu'un indicateur bancaire/assurantiel ne doit pas être assimilé automatiquement au chiffre d'affaires industriel.

### DATA-04.12 à DATA-04.16 — Extensions contrôlées

| Étape | Nouveaux PDF | Couverture cumulée | Pages du lot | Temps mesuré (s) | Tables Docling | Tables OCR | Décision |
|---|---:|---:|---:|---:|---:|---:|---|
| 04.12 | 3 | 8/45 | 206 | 1 816,082 | voir rapport | voir rapport | GO avec réserves |
| 04.13 | 5 | 13/45 | 498 | 2 584,011 | 575 | 1 | GO avec réserves |
| 04.14 | 5 | 18/45 | 414 | 2 459,864 | 397 | 3 | GO avec réserves |
| 04.15 | 10 | 28/45 | 1 190 | 6 720,339 | 1 260 | 10 | GO avec réserves |
| 04.16 | 10 | 38/45 | 1 143 | 5 877,242 | 904 | 41 | GO avec réserves |

Les étapes ajoutent la reprise document par document, les contrôles de non-régression, la surveillance mémoire et les rapports de lot. Le décompte « tables » des rapports historiques peut suivre la convention du lot ; le CSV consolidé utilise uniformément les champs actuels des 45 `pilot_result.json`.

### DATA-04.17 — MIXED et IMAGE, finalisation

- documents : cinq rapports `MIXED` TotalEnergies/Aradei et deux rapports `IMAGE` Dari Couspate ;
- point de contrôle obligatoire réussi entre Dari 2024 et Dari 2025 ;
- résultat du lot : 845 pages, 6 430,432 s, 710 tables Docling détectées et 80 tables OCR non vides ;
- Quality Gate du lot : 8 654 candidates, aucune nouvelle `VERIFIED`, 1 245 à revoir, 162 rejetées, 7 247 non contrôlées ;
- résultat : couverture 45/45, aucun document bloqué, sept `PARTIAL` ;
- décision : `GO AVEC RÉSERVES` pour préparer l'aval, sans considérer les cellules non vérifiées comme des faits.

## 6. Résultats consolidés des 45 PDF

La table complète, une ligne par couple entreprise/exercice, est [PARSING_CORPUS_SUMMARY.csv](PARSING_CORPUS_SUMMARY.csv). Elle est reconstruite à partir du manifeste, de l'audit et des 45 résultats uniques, et non par addition des rapports de lots.

### Distribution technique

| Mesure | Minimum | Médiane | Maximum |
|---|---:|---:|---:|
| Durée des composants par PDF | 287,842 s | 573,840 s | 1 421,631 s |
| Pic RSS par PDF | 1 906,27 MiB | 2 445,36 MiB | 3 512,64 MiB |

La classification des pages lors de l'audit est : 2 561 `TEXT`, 456 `IMAGE`, 1 805 `MIXED` et 53 `UNKNOWN`. La stratégie recommandée était Docling pour 31 documents, hybride pour 12 et OCR pour 2. L'exécution finale a traité 826 pages OCR, soit 106 de plus que les 720 candidates initiales, en raison des anomalies révélées après parsing.

Le statut OCR est `PARSED` pour 24 documents et `FAILED` pour 21. Ce dernier décompte ne représente pas 21 échecs documentaires : il signifie que l'étape OCR n'a pas trouvé de table utilisable dans ces cas, alors que les tables Docling peuvent rester exploitables. De même, Docling est `PARTIAL` pour 43 documents et `FAILED` pour les deux PDF `IMAGE`, qui ont été récupérés par la stratégie OCR-first.

Les documents techniquement les plus difficiles sont notamment :

- Aradei Capital 2023 : 93 pages OCR, 75 tables Docling invalides et 76 pages encore ambiguës ;
- Aradei Capital 2024 : durée maximale du corpus (1 421,631 s), 42 pages OCR et 31 tables Docling invalides ;
- TotalEnergies 2023/2025 : respectivement 52/55 pages OCR et 45 pages ambiguës dans chaque rapport ;
- Dari Couspate 2024/2025 : les deux rapports `IMAGE`, 48/49 pages OCR, Docling sans table utilisable et périmètre documentaire inconnu ;
- Stroc 2024/2025 : 30/29 pages OCR et périmètre encore inconnu.

Les difficultés observées portent sur le coût, les scans, les tableaux éclatés et l'ambiguïté des cellules, pas sur l'ouverture du PDF. Le plus fort pic mémoire appartient à Wafa Assurance 2024 (3 512,6 MiB), sans échec final.

## 7. Dépôt, données et artefacts

### Arbre utile

```text
config/
├── data/ammc_corpus.json
└── quality/ammc/                         # règles/références révisables
data/
├── raw/ammc/                             # PDF originaux + corpus/manifeste
├── extracted/ammc/                       # exports Docling/OCR et caches
├── integrated/ammc/                      # unified_document.json
├── processed/ammc/corpus_audit/          # audit et plan
└── quality/ammc/                         # annotations, revues et rapports
docs/parsing_ocr/                          # présente passation
src/moroccan_markets_ai/data/
├── collectors/
├── parsers/
└── processing/
tests/data/
├── collectors/
├── parsers/
└── processing/
```

### Catégories de fichiers

- indispensables au code : modules Python, `pyproject.toml`, `uv.lock`, configurations génériques et tests ;
- reproductibles : audits, plans, sorties Docling/OCR/intégration/qualité si les PDF et versions systèmes sont disponibles ;
- volumineux : `data/raw/` (environ 625 MiB), `data/extracted/` (408 MiB) et `data/quality/` (381 MiB) ;
- utiles à l'audit : manifestes, rapports JSON/CSV et le présent résumé ;
- à exclure de Git classique : PDF, caches massifs, artefacts d'extraction complets, DuckDB, `.env`, caches outils et temporaires.

Plusieurs artefacts JSON contiennent des chemins absolus locaux. Ils sont utiles comme preuve sur la machine d'origine mais ne sont pas tous portables. Une politique d'artefacts (release, objet storage ou Git LFS si validé par l'équipe) est nécessaire avant leur partage.

## 8. Installation et exécution

Le guide vérifié est [RUNBOOK.md](RUNBOOK.md). L'environnement observé est macOS Intel, Python 3.12.10, Docling 2.140.0, PyMuPDF 1.28.2, PyTorch 2.2.2, NumPy 1.26.4 et Tesseract 5.5.3 avec `fra`/`eng`.

Commandes principales :

```bash
brew install uv tesseract tesseract-lang
uv sync --frozen
uv run python -m moroccan_markets_ai.data.processing.corpus_pdf_audit --help
uv run python -m moroccan_markets_ai.data.parsers.pdf_parser data/raw/ammc/.../rapport.pdf
uv run python -m moroccan_markets_ai.data.processing.corpus_batch_runner --dry-run
uv run pytest -q
uv run ruff check src/ tests/
git diff --check
```

La reprise se fait via les options `--resume` des runners ; `--no-resume` force une exécution et ne doit être utilisé qu'intentionnellement. Ne pas lancer les 45 documents en parallèle sur la machine cible.

## 9. Contrats pour les autres équipes

### Data Engineering

Entrée recommandée : `unified_document.json` accompagné du rapport du Quality Gate et de l'export admissible. Ne charger comme faits financiers que les observations présentes dans `admissible_values.jsonl` produit avec la règle stricte. Conserver `source_pdf_sha256`, page, table, période, unité, périmètre et référence de validation.

Structure simplifiée réellement représentée :

```json
{
  "source_pdf": "...pdf",
  "source_sha256": "...",
  "tables": [
    {"table_id": "DOCLING_001", "page_number": 12, "source": "DOCLING"},
    {"table_id": "OCR_P012_R01", "page_number": 12, "source": "OCR"}
  ]
}
```

Les noms précis et champs optionnels doivent être lus dans chaque artefact ; cet extrait est illustratif et ne remplace pas son schéma effectif.

### Qwen3 / extraction LLM

Les entrées disponibles sont `document.md`, `document.json`, les CSV et le document intégré. Le modèle devra produire des structures Pydantic avec liens vers page/table/source, mais sa réponse restera une candidate. Il ne doit pas recevoir les quarantaines comme faits établis ni s'auto-valider à partir de sa propre sortie. Les pages/régions ambiguës doivent être dirigées vers la file de revue.

### Machine Learning

Les indicateurs prioritaires incluent chiffre d'affaires, résultat net, total actif, total passif et capitaux propres lorsque leur définition sectorielle est applicable. Le clustering exige des observations homogènes en période, unité et périmètre. Avec 89 références historiques vérifiées réparties inégalement, la couverture n'est pas encore suffisante pour supposer un panel complet 15 × 3 × indicateurs.

### Dashboard et agent conversationnel

Afficher le statut, l'entreprise, l'exercice, l'unité, le périmètre et le lien de preuve avec chaque chiffre. Les valeurs `REVIEW_REQUIRED`, `REJECTED` et `NOT_CHECKED` peuvent être montrées comme éléments de revue, jamais comme faits fiables. Les versions comparatives doivent rester séparées des valeurs « as reported » et des dernières versions disponibles.

## 10. Audit Git et préparation du push

État observé : branche `main`, remote `origin` vers `https://github.com/SafiaTirar/Moroccan_Markets_IA_Analyst.git`, worktree fortement modifié et mêlant Parsing/OCR, macroéconomie et warehouse. Aucun `add`, commit, push, reset ou nettoyage n'a été exécuté.

Le détail des fichiers, des exclusions, de la lacune `.gitignore`, des commandes ciblées et de la Pull Request est dans [GIT_HANDOVER_CHECKLIST.md](GIT_HANDOVER_CHECKLIST.md). Points bloquants avant push :

1. isoler les changements Parsing/OCR sur une branche dédiée ;
2. faire relire `pyproject.toml`/`uv.lock`, partagés avec d'autres domaines ;
3. décider où publier manifestes et petits rapports sans embarquer les artefacts volumineux ;
4. vérifier les protections `.gitignore` pour `data/extracted`, `data/integrated`, `data/quality` et DuckDB ; elles ont été ajoutées dans un correctif non destructif après le premier push ;
5. supprimer ou relativiser les chemins absolus des artefacts destinés à être partagés ;
6. obtenir l'accord explicite avant tout commit ou push.

Le premier push sur `main` avait inclus les artefacts générés et une base DuckDB. Le correctif suivant les retire du suivi tout en les conservant sur disque. Comme l'historique n'est pas réécrit, leurs blobs restent accessibles dans le commit antérieur ; cette limite est intentionnelle afin d'éviter un `push --force` non coordonné.

Message proposé :

```text
docs(parsing-ocr): document AMMC pipeline handover and corpus results
```

## 11. Limites et risques connus

### Limites maîtrisées

- documents `PARTIAL` mais techniquement traités ;
- OCR sans table utile lorsque Docling suffit ;
- reprises contrôlées par hashes et configuration ;
- traitement séquentiel et pic observé inférieur à 8 Go ;
- conservation des versions et sources au lieu d'une fusion arbitraire.

### Risques nécessitant encore une action

- concaténation de montants, colonnes décalées et signes perdus ;
- unités, périodes ou périmètres non explicités ;
- distinction social/consolidé et valeur courante/comparative ;
- dépendance au binaire Tesseract et à ses langues ;
- cache invalide si versions ou règles changent ;
- temps de retraitement complet de plusieurs heures ;
- validation financière limitée par les références humaines disponibles ;
- comportement par défaut du Quality Gate générique moins strict que celui des runners.

Le blocage pour l'aval n'est pas le parsing des PDF, désormais couvert, mais l'utilisation indifférenciée de toutes les cellules comme chiffres fiables. L'intégration Qwen3 peut être préparée seulement si la sortie reste candidate et traverse ensuite une validation indépendante.

## 12. Livrables de passation

- [HANDOVER_PARSING_OCR.md](HANDOVER_PARSING_OCR.md) — rapport complet ;
- [ARCHITECTURE.md](ARCHITECTURE.md) — diagramme, modules et seuils ;
- [RUNBOOK.md](RUNBOOK.md) — installation, commandes, reprise et dépannage ;
- [DATA_QUALITY_AND_LIMITATIONS.md](DATA_QUALITY_AND_LIMITATIONS.md) — statuts, preuves et risques ;
- [GIT_HANDOVER_CHECKLIST.md](GIT_HANDOVER_CHECKLIST.md) — publication ciblée ;
- [PARSING_CORPUS_SUMMARY.csv](PARSING_CORPUS_SUMMARY.csv) — 45 lignes de résultats réels ;
- [README.md](README.md) — index.

## 13. Critère de handover

Le corpus est prêt pour une reprise technique par l'équipe : couverture complète, provenance conservée, exécution reproductible et artefacts structurés. Il reste `GO AVEC RÉSERVES` pour une consommation financière en aval : seules les observations effectivement `VERIFIED` peuvent être utilisées comme faits, et les autres nécessitent validation, revue ou extraction sémantique suivie d'une preuve indépendante.
