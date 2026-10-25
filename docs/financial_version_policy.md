# Politique des versions financières

Les observations AMMC restent attachées au rapport dans lequel elles ont été
publiées. La clé d'une version comprend l'entreprise, l'exercice du rapport, la
période observée, l'indicateur, le périmètre comptable et la devise.

## Modes de sélection

### `AS_REPORTED`

Sélectionne uniquement la valeur publiée dans le rapport de l'exercice observé :
`report_exercise_year = observation_year`.

Si elle n'existe pas, la vue retourne `MISSING_AS_REPORTED` et une valeur `NULL`.
Une valeur comparative provenant d'un rapport ultérieur n'est jamais utilisée
comme fallback silencieux.

### `LATEST_AVAILABLE`

Sélectionne la version issue du rapport le plus récent disponible. Le résultat
indique toujours `selected_report_exercise_year`. Cette version n'est pas
qualifiée automatiquement de corrigée ou retraitée.

Si plusieurs valeurs ont la même priorité, le statut devient
`CONFLICTING_PRIORITY` et aucune valeur n'est sélectionnée arbitrairement.

### `ALL_VERSIONS`

Retourne toutes les versions `VERIFIED`, y compris les valeurs courantes et
comparatives, avec le PDF, la page, la table et la référence de validation.

## Comparabilité

Un calcul de croissance exige la même entreprise, le même indicateur, le même
périmètre comptable et la même devise. Une observation absente reste absente et
n'est jamais remplacée par zéro. Une base égale à zéro rend le taux de croissance
indéfini, même si l'écart absolu peut être présenté.

`NET_INCOME` et `NET_INCOME_GROUP_SHARE` restent deux indicateurs distincts. Les
comptes sociaux et consolidés ne sont jamais fusionnés. Les indicateurs
sectoriels ne sont pas assimilés les uns aux autres.

## Interface APP-01

`financial_analytics.query_financial()` ouvre DuckDB en lecture seule et accepte
une politique explicite. APP-01 doit afficher `selection_policy`,
`selection_status` et `selected_report_exercise_year` à côté de chaque résultat.
Pour une croissance, APP-01 doit utiliser `growth_between_years()` et afficher le
motif fourni lorsque le statut est `NOT_COMPARABLE`.
