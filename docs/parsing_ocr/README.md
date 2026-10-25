# Parsing & OCR AMMC — index de passation

Cette documentation décrit l'état réellement observé du pipeline AMMC au terme de DATA-04.17. Elle distingue la couverture documentaire, achevée sur 45 rapports, de la validation financière, encore partielle.

- [Handover complet](HANDOVER_PARSING_OCR.md)
- [Architecture du pipeline](ARCHITECTURE.md)
- [Runbook d'installation et d'exploitation](RUNBOOK.md)
- [Qualité financière et limites](DATA_QUALITY_AND_LIMITATIONS.md)
- [Checklist GitHub](GIT_HANDOVER_CHECKLIST.md)
- [Résumé des 45 rapports](PARSING_CORPUS_SUMMARY.csv)

État synthétique vérifié : 15 entreprises × 2023–2025, 45 PDF, 4 875 pages, 31 documents `TEXT`, 12 `MIXED`, 2 `IMAGE`, aucun échec technique final bloquant. Les 45 résultats documentaires sont `PARTIAL` : cela signifie qu'il reste des avertissements ou des valeurs non vérifiées, pas que les conversions ont échoué.

La règle downstream est stricte : seules les observations `VERIFIED` sont admissibles. Les tables et valeurs OCR ne deviennent jamais fiables par leur seule présence dans un CSV.
