-- 1. Couverture VERIFIED par entreprise, y compris les entreprises sans observation.
SELECT company_id, verified_observation_count, verified_indicator_count
FROM v_financial_coverage
ORDER BY verified_observation_count DESC, company_id;

-- 2. Observations financières avec distinction courant/comparatif.
SELECT company_id, observation_year, indicator, accounting_perimeter,
       normalized_value, currency, is_comparative
FROM v_financial_observations
ORDER BY company_id, observation_year, indicator;

-- 3. IPC général HCP sur les douze dernières observations disponibles.
SELECT observation_date, value, unit, base_year
FROM v_hcp_monthly_trend
WHERE category = 'Indice Général'
ORDER BY observation_date DESC
LIMIT 12;

-- 4. Indicateurs annuels World Bank 2023-2025.
SELECT observation_year, indicator, value, unit
FROM v_worldbank_annual_trend
WHERE observation_year BETWEEN 2023 AND 2025
ORDER BY observation_year, indicator;

-- 5. Comptage par granularité sans joindre IPC mensuel et inflation annuelle.
SELECT 'HCP_MONTHLY' AS series, count(*) AS observation_count
FROM fact_hcp_monthly
UNION ALL
SELECT 'WORLDBANK_ANNUAL', count(*)
FROM fact_worldbank_annual
UNION ALL
SELECT 'AMMC_VERIFIED', count(*)
FROM fact_financial_observations;

-- 6. Valeur telle que publiée dans le rapport de l'exercice, sans fallback.
SELECT company_id, observation_year, indicator, accounting_perimeter,
       normalized_value, selection_status, selected_report_exercise_year
FROM v_financial_as_reported
WHERE company_id = 's2m' AND observation_year = 2024
ORDER BY indicator;

-- 7. Dernière version disponible, sans la qualifier de correction.
SELECT company_id, observation_year, indicator, accounting_perimeter,
       normalized_value, selection_status, selected_report_exercise_year
FROM v_financial_latest_available
WHERE company_id = 's2m' AND observation_year = 2024
ORDER BY indicator;

-- 8. Divergences entre versions avec écarts calculables.
SELECT company_id, observation_year, indicator, accounting_perimeter,
       first_report_exercise_year, latest_report_exercise_year,
       first_value, latest_value, absolute_difference,
       relative_difference_pct, comparison_status
FROM v_financial_version_comparison
WHERE comparison_status = 'DIVERGENT'
ORDER BY company_id, observation_year, indicator;

