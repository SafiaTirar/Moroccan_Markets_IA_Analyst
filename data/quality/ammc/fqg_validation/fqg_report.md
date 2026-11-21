# Rapport du Financial Quality Gate — candidats AMMC

Généré le 2026-11-21T10:20:48+00:00 — schéma `fqg-handoff-1.0`.

## Synthèse

| Mesure | Valeur |
|---|---:|
| Candidats reçus | 329 |
| Structurellement valides | 329 |
| Rejetés à la validation structurelle | 0 |
| Doublons exacts | 0 |
| Groupes de valeurs en conflit | 31 |
| Candidats en conflit | 127 |
| Total actif ≠ total passif | 0 |
| **Vérifiés (VERIFIED)** | **33** |
| Confirmés PDF (PDF_CONFIRMED) | 15 |
| Rejetés (REJECTED) | 2 |
| À revoir (REVIEW_REQUIRED) | 133 |
| Non contrôlés (NOT_CHECKED) | 146 |
| **Restant à contrôler** | **294** |

Seuls les candidats `VERIFIED` sont admissibles en aval (`admissible_values.jsonl`).

## Anomalies de l'Extraction par catégorie

| Mesure | Valeur |
|---|---:|
| AUCUN_CANDIDAT_EXTRAIT | 13 |
| MONTANT_A_VERIFIER | 39 |
| TABLEAU_TO_REVIEW | 81 |

## Rapports sans candidat

- aradei_capital 2023
- aradei_capital 2024
- aradei_capital 2025
- cosumar 2023
- dari_couspate 2023
- dari_couspate 2024
- dari_couspate 2025
- stroc 2023
- stroc 2024
- stroc 2025
- totalenergies_marketing_maroc 2023
- totalenergies_marketing_maroc 2024
- totalenergies_marketing_maroc 2025

## Motifs de revue (hors INFO)

| Mesure | Valeur |
|---|---:|
| VALUE_CONFLICT | 125 |
| EQUITY_GROUP_SHARE | 32 |
| LABEL_INDICATOR_MISMATCH | 18 |
| DISTANT_OBSERVATION_YEAR | 9 |
| LINKED_AMOUNT_ANOMALY | 7 |
| MERGED_LABEL | 4 |
| MULTIPLE_AMOUNTS_IN_CELL | 3 |
| VALUE_NOT_IN_SOURCE_LINE | 2 |
| THOUSANDS_SEPARATOR_MISREAD | 2 |
| UNIT_SCALE_DIVERGENCE | 2 |

## Échantillon vérifié contre les sources (jour 4)

32 candidats, 11 entreprises, exercices 2023, 2024, 2025, 8 indicateurs.

| Mesure | Valeur |
|---|---:|
| VERIFIED | 14 |
| PDF_CONFIRMED | 13 |
| NOT_CHECKED | 0 |
| REVIEW_REQUIRED | 3 |
| REJECTED | 2 |

Décisions humaines refusées (preuve incomplète) : 0.

## Statuts par entreprise

| Entreprise | VERIFIED | PDF_CONFIRMED | NOT_CHECKED | REVIEW_REQUIRED | REJECTED |
|---|---:|---:|---:|---:|---:|
| attijariwafa_bank | 3 | 0 | 36 | 7 | 0 |
| auto_hall | 2 | 0 | 12 | 6 | 0 |
| cosumar | 1 | 2 | 18 | 19 | 0 |
| lafargeholcim_maroc | 3 | 0 | 9 | 0 | 0 |
| lesieur_cristal | 6 | 4 | 7 | 21 | 0 |
| managem | 2 | 4 | 5 | 13 | 0 |
| marsa_maroc | 3 | 0 | 33 | 28 | 0 |
| s2m | 0 | 3 | 16 | 2 | 0 |
| sonasid | 0 | 0 | 2 | 14 | 2 |
| sothema | 12 | 0 | 0 | 6 | 0 |
| wafa_assurance | 1 | 2 | 8 | 17 | 0 |
