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
- 28/32 décisions humaines associées au nouveau handoff.

### Sonasid 2023

- Exclusion du tableau DOCLING_043 pour NET_INCOME_GROUP_SHARE.
- Sélection du tableau DOCLING_042.
- Résultat net part du groupe 2023 : 65077.
- Résultat net part du groupe 2022 : 85930.
- Exclusion des faux positifs liés au résultat net par action.

### Décisions historiques non associées

Quatre décisions historiques ne correspondent plus aux candidats actuels :

- Deux décisions REJECTED concernant les anciens candidats Sonasid.
- Deux décisions REVIEW_REQUIRED concernant les capitaux propres part du groupe de Sothema.

Ces décisions restent conservées dans l'historique FQG.
Aucune réassociation automatique n'a été effectuée.

### Stroc 2024

Les six candidats narratifs conservent leurs montants, leurs textes sources et leur provenance.

La compatibilité des montants accolés à MMAD avec les contrôles FQG reste à traiter séparément.

## Configuration du corpus

Définir la variable d'environnement AMMC_CORPUS_ROOT vers le dossier contenant les rapports extraits.

Exemple PowerShell :

    $env:AMMC_CORPUS_ROOT = "CHEMIN_VERS_LE_CORPUS"

## Vérifications

- 19 tests d'extraction réussis.
- Ruff : All checks passed.
- Conservation des 13 décisions VERIFIED confirmée sur le handoff final.

## Précautions

- Tous les candidats entrants restent NOT_CHECKED.
- Aucune décision humaine VERIFIED n'est attribuée automatiquement.
- Les anciens fichiers de validation FQG ne sont pas modifiés.
- Les candidats nécessitent toujours les contrôles financiers du FQG.
