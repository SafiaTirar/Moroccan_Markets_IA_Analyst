# Rapport du Financial Quality Gate — candidats AMMC

Généré le 2026-11-24T15:33:55+00:00 — schéma `fqg-handoff-1.0`.

## Synthèse

| Mesure | Valeur |
|---|---:|
| Candidats reçus | 327 |
| Structurellement valides | 327 |
| Rejetés à la validation structurelle | 0 |
| Doublons exacts | 0 |
| Groupes de valeurs en conflit | 34 |
| Candidats en conflit | 104 |
| Total actif ≠ total passif | 0 |
| **Vérifiés (VERIFIED)** | **47** |
| Confirmés PDF (PDF_CONFIRMED) | 37 |
| Rejetés (REJECTED) | 0 |
| À revoir (REVIEW_REQUIRED) | 112 |
| Non contrôlés (NOT_CHECKED) | 131 |
| **Restant à contrôler** | **280** |

Seuls les candidats `VERIFIED` sont admissibles en aval (`admissible_values.jsonl`).

## Anomalies de l'Extraction par catégorie

| Mesure | Valeur |
|---|---:|
| AUCUN_CANDIDAT_EXTRAIT | 9 |
| MONTANT_A_VERIFIER | 45 |
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
- stroc 2025

## Motifs de revue (hors INFO)

| Mesure | Valeur |
|---|---:|
| VALUE_CONFLICT | 100 |
| DISTANT_OBSERVATION_YEAR | 8 |
| LINKED_AMOUNT_ANOMALY | 7 |
| EQUITY_GROUP_SHARE | 6 |
| MERGED_LABEL | 4 |
| UNIT_SCALE_DIVERGENCE | 4 |
| MULTIPLE_AMOUNTS_IN_CELL | 3 |
| LABEL_INDICATOR_MISMATCH | 2 |

## Échantillon vérifié contre les sources (jour 4)

28 candidats, 10 entreprises, exercices 2023, 2024, 2025, 8 indicateurs.

| Mesure | Valeur |
|---|---:|
| VERIFIED | 14 |
| PDF_CONFIRMED | 13 |
| NOT_CHECKED | 0 |
| REVIEW_REQUIRED | 1 |
| REJECTED | 0 |

Décisions humaines refusées (preuve incomplète) : 0.

Décisions humaines obsolètes (candidat absent du handoff courant, conservées et non réassociées) : 8.

## Statuts par entreprise

| Entreprise | VERIFIED | PDF_CONFIRMED | NOT_CHECKED | REVIEW_REQUIRED | REJECTED |
|---|---:|---:|---:|---:|---:|
| attijariwafa_bank | 3 | 0 | 36 | 7 | 0 |
| auto_hall | 2 | 0 | 12 | 6 | 0 |
| cosumar | 1 | 2 | 14 | 19 | 0 |
| lafargeholcim_maroc | 3 | 0 | 9 | 0 | 0 |
| lesieur_cristal | 6 | 4 | 5 | 19 | 0 |
| managem | 2 | 4 | 3 | 11 | 0 |
| marsa_maroc | 3 | 0 | 28 | 37 | 0 |
| s2m | 0 | 3 | 16 | 2 | 0 |
| sonasid | 14 | 0 | 0 | 0 | 0 |
| sothema | 12 | 0 | 0 | 0 | 0 |
| stroc | 0 | 6 | 0 | 0 | 0 |
| totalenergies_marketing_maroc | 0 | 16 | 0 | 0 | 0 |
| wafa_assurance | 1 | 2 | 8 | 11 | 0 |
