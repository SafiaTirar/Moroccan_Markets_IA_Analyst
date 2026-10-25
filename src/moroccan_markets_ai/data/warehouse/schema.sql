CREATE TABLE dim_company (
    company_id VARCHAR PRIMARY KEY,
    issuer VARCHAR NOT NULL,
    sector VARCHAR NOT NULL,
    aliases_json JSON NOT NULL
);

CREATE TABLE fact_financial_observations (
    verification_reference_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES dim_company(company_id),
    report_exercise_year SMALLINT NOT NULL CHECK (report_exercise_year BETWEEN 1900 AND 2100),
    observation_period VARCHAR NOT NULL,
    observation_year SMALLINT NOT NULL CHECK (observation_year BETWEEN 1900 AND 2100),
    is_comparative BOOLEAN NOT NULL,
    indicator VARCHAR NOT NULL,
    normalized_value DECIMAL(38, 6) NOT NULL,
    currency VARCHAR NOT NULL,
    source_scale BIGINT NOT NULL CHECK (source_scale > 0),
    accounting_perimeter VARCHAR NOT NULL
        CHECK (accounting_perimeter IN ('SOCIAL', 'CONSOLIDATED')),
    source_pdf_sha256 VARCHAR NOT NULL CHECK (length(source_pdf_sha256) = 64),
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    table_id VARCHAR NOT NULL,
    verification_status VARCHAR NOT NULL CHECK (verification_status = 'VERIFIED'),
    UNIQUE (
        company_id,
        report_exercise_year,
        observation_year,
        indicator,
        accounting_perimeter,
        currency
    )
);

CREATE TABLE fact_hcp_monthly (
    observation_date DATE NOT NULL,
    indicator VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    value DECIMAL(18, 6),
    unit VARCHAR NOT NULL,
    base_year SMALLINT NOT NULL CHECK (base_year BETWEEN 1900 AND 2100),
    source_url VARCHAR NOT NULL,
    source_file VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL CHECK (length(source_sha256) = 64),
    source_sheet VARCHAR NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (observation_date, indicator, category)
);

CREATE TABLE fact_worldbank_annual (
    country VARCHAR NOT NULL,
    observation_year SMALLINT NOT NULL CHECK (observation_year BETWEEN 1900 AND 2100),
    indicator VARCHAR NOT NULL,
    value DECIMAL(38, 18),
    unit VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    source_file VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL CHECK (length(source_sha256) = 64),
    PRIMARY KEY (country, observation_year, indicator)
);

CREATE TABLE ingestion_runs (
    run_id VARCHAR PRIMARY KEY,
    schema_version VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NOT NULL,
    status VARCHAR NOT NULL CHECK (status = 'COMMITTED')
);

CREATE TABLE ingestion_sources (
    run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    source_name VARCHAR NOT NULL,
    source_path VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL CHECK (length(source_sha256) = 64),
    input_rows INTEGER NOT NULL CHECK (input_rows >= 0),
    loaded_rows INTEGER NOT NULL CHECK (loaded_rows >= 0),
    quarantined_rows INTEGER NOT NULL CHECK (quarantined_rows >= 0),
    PRIMARY KEY (run_id, source_name)
);

CREATE VIEW v_financial_observations AS
SELECT
    f.company_id,
    c.issuer,
    c.sector,
    f.report_exercise_year,
    f.observation_period,
    f.observation_year,
    f.is_comparative,
    f.indicator,
    f.normalized_value,
    f.currency,
    f.source_scale,
    f.accounting_perimeter,
    f.source_pdf_sha256,
    f.page_number,
    f.table_id,
    f.verification_reference_id,
    f.verification_status
FROM fact_financial_observations AS f
JOIN dim_company AS c USING (company_id);

CREATE VIEW v_hcp_monthly_trend AS
SELECT
    observation_date,
    indicator,
    category,
    value,
    unit,
    base_year
FROM fact_hcp_monthly;

CREATE VIEW v_worldbank_annual_trend AS
SELECT
    country,
    observation_year,
    indicator,
    value,
    unit
FROM fact_worldbank_annual;

CREATE VIEW v_financial_coverage AS
SELECT
    c.company_id,
    c.issuer,
    c.sector,
    count(f.verification_reference_id) AS verified_observation_count,
    count(DISTINCT f.indicator) AS verified_indicator_count,
    min(f.observation_year) AS first_observation_year,
    max(f.observation_year) AS last_observation_year
FROM dim_company AS c
LEFT JOIN fact_financial_observations AS f USING (company_id)
GROUP BY c.company_id, c.issuer, c.sector;

CREATE VIEW v_financial_all_versions AS
SELECT
    f.company_id,
    c.issuer,
    c.sector,
    f.observation_year,
    f.indicator,
    f.accounting_perimeter,
    f.currency,
    'ALL_VERSIONS' AS selection_policy,
    'AVAILABLE' AS selection_status,
    count(*) OVER (
        PARTITION BY f.company_id, f.observation_year, f.indicator,
                     f.accounting_perimeter, f.currency
    ) AS available_version_count,
    1 AS priority_candidate_count,
    f.report_exercise_year AS selected_report_exercise_year,
    f.observation_period,
    f.is_comparative,
    f.normalized_value,
    f.source_scale,
    f.source_pdf_sha256,
    f.page_number,
    f.table_id,
    f.verification_reference_id,
    f.verification_status
FROM fact_financial_observations AS f
JOIN dim_company AS c USING (company_id);

CREATE VIEW v_financial_as_reported AS
WITH keys AS (
    SELECT
        company_id,
        observation_year,
        indicator,
        accounting_perimeter,
        currency,
        count(*) AS available_version_count
    FROM fact_financial_observations
    GROUP BY ALL
), candidates AS (
    SELECT *
    FROM fact_financial_observations
    WHERE report_exercise_year = observation_year
), selected AS (
    SELECT
        k.*,
        count(c.verification_reference_id) AS priority_candidate_count,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.report_exercise_year) END AS selected_report_exercise_year,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.observation_period) END AS observation_period,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN bool_and(c.is_comparative) END AS is_comparative,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.normalized_value) END AS normalized_value,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.source_scale) END AS source_scale,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.source_pdf_sha256) END AS source_pdf_sha256,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.page_number) END AS page_number,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.table_id) END AS table_id,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.verification_reference_id) END AS verification_reference_id,
        CASE WHEN count(c.verification_reference_id) = 1
             THEN max(c.verification_status) END AS verification_status
    FROM keys AS k
    LEFT JOIN candidates AS c USING (
        company_id, observation_year, indicator, accounting_perimeter, currency
    )
    GROUP BY ALL
)
SELECT
    s.company_id,
    c.issuer,
    c.sector,
    s.observation_year,
    s.indicator,
    s.accounting_perimeter,
    s.currency,
    'AS_REPORTED' AS selection_policy,
    CASE
        WHEN s.priority_candidate_count = 0 THEN 'MISSING_AS_REPORTED'
        WHEN s.priority_candidate_count > 1 THEN 'CONFLICTING_PRIORITY'
        ELSE 'AVAILABLE'
    END AS selection_status,
    s.available_version_count,
    s.priority_candidate_count,
    s.selected_report_exercise_year,
    s.observation_period,
    s.is_comparative,
    s.normalized_value,
    s.source_scale,
    s.source_pdf_sha256,
    s.page_number,
    s.table_id,
    s.verification_reference_id,
    s.verification_status
FROM selected AS s
JOIN dim_company AS c USING (company_id);

CREATE VIEW v_financial_latest_available AS
WITH keys AS (
    SELECT
        company_id,
        observation_year,
        indicator,
        accounting_perimeter,
        currency,
        count(*) AS available_version_count,
        max(report_exercise_year) AS latest_report_exercise_year
    FROM fact_financial_observations
    GROUP BY ALL
), selected AS (
    SELECT
        k.*,
        count(f.verification_reference_id) AS priority_candidate_count,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.report_exercise_year) END AS selected_report_exercise_year,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.observation_period) END AS observation_period,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN bool_and(f.is_comparative) END AS is_comparative,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.normalized_value) END AS normalized_value,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.source_scale) END AS source_scale,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.source_pdf_sha256) END AS source_pdf_sha256,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.page_number) END AS page_number,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.table_id) END AS table_id,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.verification_reference_id) END AS verification_reference_id,
        CASE WHEN count(f.verification_reference_id) = 1
             THEN max(f.verification_status) END AS verification_status
    FROM keys AS k
    LEFT JOIN fact_financial_observations AS f
      ON f.company_id = k.company_id
     AND f.observation_year = k.observation_year
     AND f.indicator = k.indicator
     AND f.accounting_perimeter = k.accounting_perimeter
     AND f.currency = k.currency
     AND f.report_exercise_year = k.latest_report_exercise_year
    GROUP BY ALL
)
SELECT
    s.company_id,
    c.issuer,
    c.sector,
    s.observation_year,
    s.indicator,
    s.accounting_perimeter,
    s.currency,
    'LATEST_AVAILABLE' AS selection_policy,
    CASE WHEN s.priority_candidate_count > 1
         THEN 'CONFLICTING_PRIORITY' ELSE 'AVAILABLE' END AS selection_status,
    s.available_version_count,
    s.priority_candidate_count,
    s.selected_report_exercise_year,
    s.observation_period,
    s.is_comparative,
    s.normalized_value,
    s.source_scale,
    s.source_pdf_sha256,
    s.page_number,
    s.table_id,
    s.verification_reference_id,
    s.verification_status
FROM selected AS s
JOIN dim_company AS c USING (company_id);

CREATE VIEW v_financial_version_comparison AS
WITH versions AS (
    SELECT
        company_id,
        observation_year,
        indicator,
        accounting_perimeter,
        currency,
        count(*) AS version_count,
        count(DISTINCT normalized_value) AS distinct_value_count,
        min(report_exercise_year) AS first_report_exercise_year,
        max(report_exercise_year) AS latest_report_exercise_year,
        arg_min(normalized_value, report_exercise_year) AS first_value,
        arg_max(normalized_value, report_exercise_year) AS latest_value,
        bool_or(report_exercise_year = observation_year) AS has_as_reported
    FROM fact_financial_observations
    GROUP BY ALL
)
SELECT
    v.*,
    v.latest_value - v.first_value AS signed_difference,
    abs(v.latest_value - v.first_value) AS absolute_difference,
    CASE WHEN v.first_value <> 0
         THEN CAST(
             ((v.latest_value - v.first_value) / abs(v.first_value)) * 100
             AS DECIMAL(38, 12)
         )
         ELSE NULL END AS relative_difference_pct,
    CASE
        WHEN v.distinct_value_count > 1 THEN 'DIVERGENT'
        WHEN v.version_count > 1 THEN 'CONSISTENT_MULTIPLE_VERSIONS'
        ELSE 'SINGLE_VERSION'
    END AS comparison_status
FROM versions AS v;

