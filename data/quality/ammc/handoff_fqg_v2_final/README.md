# Handoff FQG V2 — Version corrigée

## Objectif

Livrer les candidats financiers AMMC corrigés au Financial Quality Gate (FQG), tout en préservant les décisions humaines existantes.

## Résultats

- Rapports traités : 45
- Candidats financiers : 327
- Anomalies : 135
- Candidats structurellement valides : 327/327
- Rejets structurels : 0

## Corrections apportées

### Conservation des validations humaines

Les identifiants historiques des candidats inchangés sont préservés en conservant leur représentation numérique d'origine.

Résultats :
- 13/13 décisions VERIFIED retrouvées.
- 14/14 décisions PDF_CONFIRMED retrouvées.
- 28/32 décisions humaines associées au nouveau handoff (sur les 32 décisions du jour 4).

Après la première passe de validation FQG (Lesieur Cristal, Managem, Sothema), `human_decisions.json` comptait 57 décisions : 49 sont associées au handoff v2 final, dont les 33 VERIFIED et les 15 PDF_CONFIRMED.

### Sonasid 2023

- Exclusion du tableau DOCLING_043 pour NET_INCOME_GROUP_SHARE.
- Sélection du tableau DOCLING_042.
- Résultat net part du groupe 2023 : 65077.
- Résultat net part du groupe 2022 : 85930.
- Exclusion des faux positifs liés au résultat net par action.

### Décisions historiques non associées

Huit décisions humaines ne correspondent plus aux candidats actuels :

- Deux décisions REJECTED concernant les anciens candidats Sonasid (n° 28 et 29, jour 4).
- Deux décisions REVIEW_REQUIRED concernant les capitaux propres part du groupe de Sothema 2025 (n° 30 et 32, jour 4).
- Quatre décisions REVIEW_REQUIRED de même nature pour Sothema 2023 et 2024 (n° 54 à 57, première passe FQG), devenues obsolètes avec la correction du mapping EQUITY.

Ces décisions restent conservées dans `data/quality/ammc/fqg_validation/human_decisions.json`.
Le FQG les liste dans `obsolete_human_decisions.json` (statut `OBSOLETE_NOT_REASSOCIATED`) : elles ne sont ni refusées, ni réassociées automatiquement.

### Stroc 2024

Les six candidats narratifs conservent leurs montants, leurs textes sources et leur provenance.

La compatibilité des montants accolés à MMAD avec les contrôles FQG est désormais traitée : le FQG lit les montants narratifs suivis d'une unité (`87MMAD`, `-14MMAD`, `- 12,5MMAD`), avec leur signe et leur virgule décimale, sans modifier le texte source. Les six candidats Stroc ne reçoivent plus `VALUE_NOT_IN_SOURCE_LINE`.

## Configuration du corpus

Définir la variable d'environnement AMMC_CORPUS_ROOT vers le dossier contenant les rapports extraits.

Exemple PowerShell :

    $env:AMMC_CORPUS_ROOT = "CHEMIN_VERS_LE_CORPUS"

## Vérifications

- 19 tests d'extraction réussis.
- 91 tests FQG réussis (`uv run pytest tests/data/parsers -k fqg`), dont un test des critères d'acceptation sur ce handoff.
- Ruff : All checks passed (`uv run ruff check src/ tests/`).
- Conservation des 13 décisions VERIFIED confirmée sur le handoff final.
- Résultat FQG sur ce handoff : 327 candidats valides, 47 VERIFIED, 37 PDF_CONFIRMED, 0 REJECTED actif, 280 restant à contrôler, 0 décision refusée, 8 décisions obsolètes.
- Preuve CI : workflow GitHub Actions `.github/workflows/fqg-ci.yml`.

## Précautions

- Tous les candidats entrants restent NOT_CHECKED.
- Aucune décision humaine VERIFIED n'est attribuée automatiquement.
- Les anciens fichiers de validation FQG ne sont pas modifiés.
- Les candidats nécessitent toujours les contrôles financiers du FQG.
