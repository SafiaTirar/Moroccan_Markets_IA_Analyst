# Contrat d'intégration — Financial Quality Gate × candidats AMMC

**Statut :** v1.2 (jour 1), validé sur les fichiers réels du handoff et sur `schemas.py` de l'Extraction.
**Entrées :** `data/quality/ammc/handoff_fqg_45/` (handover Extraction)
**Sorties :** `data/quality/ammc/fqg_validation/`

## 1. Objet

Ce document fixe le format des candidats financiers transmis par l'Extraction,
le format des décisions produites par le Financial Quality Gate (FQG) et les
règles de changement de statut. Les candidats restent des **candidats** : rien
n'est considéré comme un fait financier avant le statut `VERIFIED`.

## 2. Audit de l'existant

| Constat | Conséquence pour l'intégration |
|---|---|
| `financial_quality_gate.py` (1 251 lignes, schéma 2.4) évalue un `unified_document.json`, cellule par cellule. Il ne lit pas de liste de candidats. | Il faut ajouter un **mode « handoff »** (chargeur + contrôles) sans casser le mode actuel. |
| Les règles sont des `dataclass` (`QualityRules`) et non des modèles Pydantic. | Les modèles Pydantic du handoff sont créés dans `fqg_handoff_loader.py`. Le module existant n'est pas modifié. |
| `QUALITY_STATUSES` = `VERIFIED`, `REVIEW_REQUIRED`, `REJECTED`, `NOT_CHECKED`. `PDF_CONFIRMED` n'existe pas. | `PDF_CONFIRMED` est ajouté **uniquement** pour le mode handoff (voir § 5). |
| `QualityRules.downstream_allowed_statuses` vaut `("VERIFIED", "NOT_CHECKED")` par défaut. | **Risque.** Le mode handoff impose `("VERIFIED",)` en dur. |
| Le CLI attend `integrated_document` en argument positionnel. | Le mode handoff s'active avec `--candidates`. L'argument positionnel devient optionnel (voir § 7). |
| Le registre `financial_indicator_registry.json` définit 14 codes et 4 profils (GENERAL_CORPORATE, BANKING, INSURANCE, REAL_ESTATE). | `indicator` est validé contre ce registre. La compatibilité indicateur/profil devient un contrôle. |
| `financial_version_policy.md` : la clé d'une version est entreprise + exercice du rapport + période + indicateur + périmètre + devise. | La déduplication et les conflits utilisent cette clé. On ne fusionne jamais social et consolidé. |

### Compatibilité avec les modèles Pydantic de l'Extraction

Fichier : `src/moroccan_markets_ai/extraction/schemas.py` (branche `feature/qwen3-pydantic-extraction`),
classe `CandidatFinancier(IndicateurFinancier)`.

| Champ du handoff | `CandidatFinancier` | `HandoffCandidate` (FQG) |
|---|---|---|
| `nom`, `annee`, `ligne_source`, `entreprise` | `str`/`int` | identiques |
| `valeur` | `float` | `Decimal` (via `repr(float)`, sans perte d'arrondi) |
| `unite`, `perimetre` | `str \| None` | identiques |
| `source_table`, `source_page`, `source_ligne` | `str`/`int` | identiques + page ≥ 1, ligne ≥ 0 |
| `validation_status` | `str = "NOT_CHECKED"` (non contrôlé) | `NOT_CHECKED` seul accepté en entrée |
| `indicator` | `str \| None = None` (facultatif) | **obligatoire**, vérifié dans le registre |
| `report_year` | **absent** | **obligatoire**, ∈ {2023, 2024, 2025} |

Constats :
1. `report_year` est livré dans `candidats_fqg.json` mais absent du modèle :
   valider avec `CandidatFinancier` l'ignorerait silencieusement, alors qu'il
   est indispensable pour distinguer valeur publiée et valeur comparative
   (`financial_version_policy.md`).
2. `indicator` est facultatif côté Extraction ; il est obligatoire côté FQG.
3. `CandidatFinancier` ne contrôle ni plages d'années, ni pages, ni statut :
   un candidat `validation_status="VERIFIED"` serait accepté.

Décision : le FQG valide avec `HandoffCandidate`, qui reprend exactement les
mêmes noms de champs (compatibilité totale) et ajoute les contrôles.
Recommandation à l'Extraction : ajouter `report_year: int` et rendre
`indicator` obligatoire dans `CandidatFinancier`.

## 3. Format d'entrée — `candidats_fqg.json`

Une liste JSON d'objets (329 attendus). Les champs viennent du handover Extraction (§ 7).

| Champ | Type attendu | Obligatoire | Règle |
|---|---|---|---|
| `entreprise` | str non vide | oui | doit exister dans `config/data/ammc_corpus.json` |
| `nom` | str | oui | libellé lisible de l'indicateur |
| `indicator` | str | oui | un des 14 codes du registre |
| `annee` | int | oui | année **observée**, entre 2000 et `report_year` |
| `report_year` | int | oui | ∈ {2023, 2024, 2025} |
| `valeur` | nombre ou chaîne numérique | oui | convertie en `Decimal`. La valeur brute est **conservée**. |
| `ligne_source` | str | oui | libellé de la ligne dans le tableau |
| `source_table` | str | oui | `DOCLING_nnn` ou `OCR_...` |
| `source_page` | int ≥ 1 | oui | page physique du PDF |
| `source_ligne` | int ≥ 0 | oui | index de ligne dans le CSV |
| `validation_status` | str | oui | doit valoir `NOT_CHECKED` à l'entrée |
| `unite` | str ou `null` | non | `null` signalé, **jamais inventé** |
| `perimetre` | str ou `null` | non | `SOCIAL` / `CONSOLIDE` / `null`, `null` signalé |

Champs inconnus : ils sont conservés dans `original` et signalés. Ce n'est pas un rejet.

### Examen des données réelles (handoff du 07/11/2026)

**Candidats : 329**, couvrant 11 entreprises sur 15 (rapports 2023 : 99, 2024 : 116, 2025 : 114).

| Indicateur | Nb | Indicateur | Nb |
|---|---:|---|---:|
| EQUITY | 140 | OPERATING_INCOME | 17 |
| NET_INCOME | 49 | REVENUE | 13 |
| TOTAL_LIABILITIES_AND_EQUITY | 48 | CUSTOMER_LOANS | 8 |
| NET_INCOME_GROUP_SHARE | 28 | CUSTOMER_DEPOSITS | 8 |
| TOTAL_ASSETS | 18 | *5 codes absents* | 0 |

Les 5 codes absents sont BANKING_NET_REVENUE, INSURANCE_REVENUE, WRITTEN_PREMIUMS, RENTAL_INCOME et EBITDA.

Constats :
- les 13 champs sont présents et bien typés pour les 329 candidats. `valeur` est toujours un `float` JSON ;
- `unite` et `perimetre` valent `null` pour **329/329** candidats ;
- écart entre `report_year` et `annee` : 0 an pour 157 candidats (exercice courant), 1 an pour 163 (comparatif) et 2 ans pour 9 (6 Attijariwafa rapport 2023, `DOCLING_085`/`DOCLING_086`, colonnes 2021 ; 3 Wafa Assurance rapport 2025, `DOCLING_032`, colonnes 2023, colonnes probablement décalées) ;
- aucun doublon exact. Chaque ligne source donne en général 2 candidats, un pour l'année N et un pour N-1 (161 lignes en donnent 2, 7 lignes en donnent 1) ;
- `EQUITY` est hétérogène : 18 candidats sont en réalité « TOTAL CAPITAUX PROPRES ET PASSIF », c'est-à-dire un total de bilan. D'autres libellés relèvent de la part du groupe, des actionnaires de la société mère ou de l'ensemble consolidé (point déjà signalé dans la PR #2) ;
- certaines valeurs sont suspectes : séparateur des milliers lu comme décimale (`85.712`, Sonasid), libellés fusionnés (Marsa `DOCLING_071`), échelles différentes selon les tableaux d'un même rapport (ex. Cosumar 2024 : `DOCLING_070` en dirhams, 4 684 807 359,45 ; `DOCLING_102` en millions, 5 878,5), sans unité indiquée dans les candidats.

**Anomalies : 133**, de trois formes distinctes :

| `raison` | Nb | Champs |
|---|---:|---|
| `TABLEAU_TO_REVIEW` | 81 | entreprise, report_year, table_id, pages, detail |
| `MONTANT_A_VERIFIER` | 39 | entreprise, report_year, table_id, ligne, indicator, observation_year, cellule_source |
| `AUCUN_CANDIDAT_EXTRAIT` | 13 | entreprise, report_year, detail |

Rattachement d'une anomalie à un candidat : `MONTANT_A_VERIFIER` se rattache par (entreprise, report_year, table_id = source_table, ligne = source_ligne, indicator). `TABLEAU_TO_REVIEW` se rattache par (entreprise, report_year, table_id). `AUCUN_CANDIDAT_EXTRAIT` alimente la liste des rapports sans candidat.

### Champs obligatoires, facultatifs, incompatibles

- **Obligatoires :** entreprise, nom, indicator, annee, report_year, valeur, ligne_source, source_table, source_page, source_ligne, validation_status.
- **Facultatifs** (`null` toléré mais signalé) : unite, perimetre.
- **Incompatibles avec le FQG existant :**
  1. noms français (`annee`, `valeur`…) au lieu de noms anglais : on passe par la table de correspondance du § 6 ;
  2. `valeur` est un `float`, ce qui fait perdre la représentation d'origine : on convertit avec `Decimal(repr(float))` et on garde `ligne_source` comme preuve textuelle ;
  3. il manque `source_pdf_sha256` et la monnaie, pourtant exigés pour un `VERIFIED` par le handover Parsing : ils doivent être ajoutés lors de la vérification humaine (champ `evidence`) ;
  4. les anomalies utilisent `table_id`/`ligne`/`observation_year`, alors que les candidats utilisent `source_table`/`source_ligne`/`annee`.

### `anomalies.json` et `bilan.json`

Ces fichiers sont lus tels quels, en lecture seule. Ils servent à rattacher les
anomalies aux candidats, à les compter par catégorie et à lister les rapports
sans candidat.

## 4. Identifiant stable

`candidate_id` = SHA-256 (16 premiers caractères hexadécimaux) de
`entreprise|report_year|indicator|annee|source_table|source_page|source_ligne|valeur`.

L'index d'origine (`input_index`) est aussi conservé. Deux candidats identiques
gardent des `input_index` différents : on ne perd rien en silence.

## 5. Statuts et transitions

| Statut | Sens | Qui le pose |
|---|---|---|
| `NOT_CHECKED` | structurellement valide, aucun problème détecté, aucune preuve externe | automatique |
| `REVIEW_REQUIRED` | ambiguïté, conflit, contexte manquant ou anomalie liée | automatique |
| `REJECTED` | invalide (schéma, valeur non numérique, année impossible, doublon exact en surplus, discordance prouvée) | automatique ou humain |
| `PDF_CONFIRMED` | montant et signe vus dans le PDF à la page et ligne citées, mais unité, périmètre ou colonne-année pas tous prouvés | **humain uniquement** |
| `VERIFIED` | montant, signe, année/colonne, unité, périmètre et entreprise confirmés avec une preuve | **humain uniquement** |

Transitions autorisées :

```text
(entrée) NOT_CHECKED ──auto──► NOT_CHECKED | REVIEW_REQUIRED | REJECTED
REVIEW_REQUIRED / NOT_CHECKED ──humain + preuve──► PDF_CONFIRMED | VERIFIED | REJECTED
PDF_CONFIRMED ──humain + unité & périmètre prouvés──► VERIFIED
```

Règles fermes :
1. Le code n'écrit **jamais** `VERIFIED` ni `PDF_CONFIRMED` de lui-même. Ces statuts n'arrivent que par un fichier de décisions humaines avec une preuve.
2. Les règles automatiques ne peuvent que **rétrograder** un statut, jamais le promouvoir.
3. Seul `VERIFIED` est admissible en aval (`downstream_allowed_statuses = ["VERIFIED"]`).
4. Une décision `VERIFIED` sans `evidence` complète (page, table, valeur lue, unité, périmètre, vérificateur, date) est refusée.
5. Entre deux sources concurrentes, aucune n'est supprimée. Elles sont toutes marquées `REVIEW_REQUIRED` avec un `conflict_group` commun.

## 6. Format de sortie — `data/quality/ammc/fqg_validation/`

| Fichier | Contenu |
|---|---|
| `decisions.jsonl` | une ligne par candidat d'entrée (329), quel que soit le statut |
| `verified_sample.json` | décisions humaines `VERIFIED` (et `PDF_CONFIRMED`), avec preuve |
| `rejected_sample.json` | candidats `REJECTED`, avec raison |
| `review_required.json` | candidats `REVIEW_REQUIRED`, avec raisons |
| `admissible_values.jsonl` | **uniquement** `VERIFIED` |
| `fqg_report.json` / `fqg_report.md` | rapport récapitulatif (jour 5) |

Structure d'une décision :

```json
{
  "candidate_id": "3f2a9c1b7e0d4a12",
  "input_index": 41,
  "original": { "...": "candidat d'entrée intact" },
  "normalized": {
    "company": "marsa_maroc", "indicator": "TOTAL_ASSETS",
    "observation_year": 2025, "report_year": 2025,
    "value": "9332718.0", "unit": null, "scope": null,
    "source_table": "DOCLING_049", "source_page": 121, "source_row": 13
  },
  "status": "REVIEW_REQUIRED",
  "reasons": [{"code": "MISSING_UNIT", "severity": "INFO", "message": "..."}],
  "duplicate_group": null,
  "conflict_group": "c-7",
  "linked_anomalies": [],
  "evidence": null,
  "schema_version": "fqg-handoff-1.0"
}
```

Correspondance des champs : `entreprise→company`, `annee→observation_year`,
`valeur→value`, `unite→unit`, `perimetre→scope`, `source_ligne→source_row`.

## 7. Commande

```bash
uv run python -m moroccan_markets_ai.data.parsers.financial_quality_gate \
  --candidates data/quality/ammc/handoff_fqg_45/candidats_fqg.json \
  --anomalies  data/quality/ammc/handoff_fqg_45/anomalies.json \
  --output     data/quality/ammc/fqg_validation
```

L'ancien usage (`... financial_quality_gate <unified_document.json>`) reste inchangé.

## 8. Invariants testés

- `len(decisions) == len(candidats)`, sans aucune perte silencieuse ;
- aucun `VERIFIED` ni `PDF_CONFIRMED` sans fichier de décisions humaines ;
- la valeur brute d'origine est toujours présente dans `original` ;
- aucun chemin absolu ni chemin Windows dans le code. Tous les chemins sont paramétrables.