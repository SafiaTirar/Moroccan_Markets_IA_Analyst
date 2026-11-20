# Handoff FQG — Version 2

## Corrections apportées

- Correction des séparateurs numériques de Sonasid 2023.
- Amélioration du mapping de l'indicateur EQUITY.
- Correction de la détection des libellés financiers de TotalEnergies.
- Intégration d'un fallback narratif hybride Qwen3 + Regex + Pydantic pour Stroc 2024.
- Ajout de 16 tests de non-régression réussis.

## Résultats

- 45 rapports traités.
- 323 candidats financiers.
- 135 anomalies.
- 323 candidats structurellement compatibles avec le chargeur FQG.
- 0 rejet structurel.

## Limites

- Toutes les observations sont transmises au statut NOT_CHECKED.
- Les unités et périmètres restent inconnus pour certains candidats.
- Le fallback narratif est limité au pilote Stroc 2024.
- L'identifiant NARRATIVE_P005 désigne une source narrative et non un tableau CSV.
- La compatibilité des sources narratives avec les contrôles en aval du FQG reste à confirmer.
- Certains rapports ne produisent toujours aucun candidat.
- Les anciens candidats et les décisions VERIFIED sont conservés sans modification.