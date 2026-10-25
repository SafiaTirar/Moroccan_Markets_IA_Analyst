# Qualité financière et limites

## Deux niveaux à ne pas confondre

Le statut documentaire indique si le pipeline a produit des artefacts structurés. Le statut financier indique si une valeur est justifiée par une référence fiable. Un document `PARTIAL` peut être techniquement exploitable tout en contenant des tables incomplètes ; un CSV parfaitement lisible peut rester `NOT_CHECKED`.

Les 45 documents annuels terminent actuellement en `PARTIAL`. Aucun n'a de page en échec ni de table intégrée sans référence de page. Cela ne signifie pas que leurs 92 204 valeurs candidates sont validées.

## Statuts financiers

- `VERIFIED` : une cellule unique correspond exactement à une référence fiable et au contexte contrôlé.
- `REVIEW_REQUIRED` : une ambiguïté structurelle, contextuelle ou une divergence impose une revue.
- `REJECTED` : la cellule numérique ne peut pas être interprétée de manière fiable ou une règle explicite confirme son rejet.
- `NOT_CHECKED` : valeur techniquement extraite mais sans preuve externe suffisante.

Dans les orchestrateurs du corpus :

```text
downstream_allowed_statuses = ["VERIFIED"]
```

`admissible_values.jsonl` ne doit donc contenir aucune valeur non vérifiée. `load_admissible_values` revalide les hashes, le chemin du flux et chaque statut avant lecture.

## Ce qui est nécessaire pour VERIFIED

La validation de référence contrôle : identité de l'entreprise, exercice/période, indicateur et libellé source, montant, signe, devise, unité et facteur d'échelle, périmètre `SOCIAL` ou `CONSOLIDATED`, page, table, SHA-256 du PDF et preuves `TEXT_SEARCH`/`VISUAL_REGION`. La preuve doit venir du PDF original ; Docling, OCR ou un futur LLM ne peut pas servir de référence à sa propre sortie.

Une égalité actif/passif, un sous-total correct ou deux occurrences concordantes sont des contrôles complémentaires. Le code les classe `NOT_CHECKED` en cas de réussite arithmétique : ils ne suffisent jamais seuls à promouvoir une valeur.

## Contrôles génériques

Le Quality Gate :

- parse les formats marocains, espaces, virgules, points de milliers et parenthèses ;
- signale montants trop longs, séparateurs décimaux incohérents, cellules non interprétables, libellés absents ou bruités ;
- détecte unités `DH`, `MAD`, `KDH`, `MDH` et périodes quand elles sont réellement présentes ;
- peut appliquer comparaisons, sous-totaux et règles de signe seulement avec unité, période et périmètre complets ;
- associe CSV, SHA, page, coordonnées OCR et bbox de cellule lorsque disponibles ;
- met en quarantaine toute valeur hors des statuts autorisés.

## Cas réels documentés

- RCI Finance Maroc : 18 montants concaténés identifiés lors des pilotes initiaux et mis en quarantaine.
- SRM : trois lignes chiffrées sans libellé, donc non admissibles.
- Stroc : tables scannées, structures OCR ambiguës ; les années 2023–2025 restent financièrement non vérifiées.
- Dari Couspate 2025, page PDF 34 (page imprimée 35) : `818 399 882` est devenu `8 183 993 882` dans le CSV OCR.
- Dari 2025 : plusieurs parenthèses de signe du tableau des flux sont perdues ou transformées.
- S2M 2024 : deux versions VERIFIED divergentes ont été conservées dans le warehouse lors d'une mission ultérieure, car valeur publiée dans le rapport d'origine et comparatif du rapport suivant ne sont pas interchangeables.
- Les rapports peuvent contenir à la fois des comptes sociaux et consolidés. Le détecteur attache ses preuves aux pages et n'impose pas un périmètre unique à toutes les tables.

## Résultats consolidés

Le résumé des 45 documents se trouve dans [PARSING_CORPUS_SUMMARY.csv](PARSING_CORPUS_SUMMARY.csv).

- 92 204 valeurs candidates dans les rapports qualité stricts des 45 résultats uniques ;
- 24 804 `REVIEW_REQUIRED`, 2 178 `REJECTED`, 65 222 `NOT_CHECKED`, 0 `VERIFIED` dans ces rapports stricts initiaux ;
- les validations de référence sont enregistrées séparément et totalisent 89 observations `VERIFIED` préexistantes à DATA-04.17 ;
- DATA-04.17 ajoute 8 654 candidates mais 0 nouvelle `VERIFIED` ;
- ne pas additionner les 89 aux statuts des rapports stricts comme s'il s'agissait du même snapshot : les validations de référence sont des annotations dérivées séparées.

## Limites maîtrisées

- OCR : concaténations, perte de signe, colonnes supplémentaires, libellés fragmentés.
- Contexte : unité ou période souvent absente de la cellule ; propagation automatique interdite.
- Comparatifs : l'année d'observation et l'année du rapport source sont distinctes.
- Secteurs : revenus bancaires/assurantiels ne sont pas assimilés au chiffre d'affaires industriel.
- Performance : environ 29 983 s (8 h 19 min) de temps composant cumulé enregistré, médiane 574 s/document ; exécution strictement séquentielle.
- Mémoire : médiane 2 445 MiB, maximum 3 512,6 MiB ; la valeur dépend du système et de l'historique RSS du worker.
- Reproductibilité : un cache n'est réutilisé que si empreintes et artefacts concordent, mais les chemins absolus présents dans certains rapports rendent les artefacts locaux non portables tels quels.

## Problèmes bloquants versus non bloquants

Bloquant pour une valeur : unité inconnue, périmètre inconnu, période ambiguë, montant concaténé, signe perdu, référence PDF absente ou hash différent. Non bloquant pour la couverture documentaire : absence de table OCR exploitable lorsque Docling fournit déjà des tables valides, avertissements de structure conservés, document `PARTIAL` avec provenance complète.

Les deux rapports `IMAGE` Dari 2024/2025 sont techniquement traités mais nécessitent une revue humaine ciblée avant toute utilisation financière. Aucun des 45 PDF n'est techniquement bloqué.
