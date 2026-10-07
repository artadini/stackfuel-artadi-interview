-- Joins in the marts must not multiply or lose rows: each fact has exactly as many rows as its
-- staging model after the documented filter (not quarantined, not deleted).
select 'fct_enrollments' as model_name, mart.n as mart_rows, stg.n as staging_rows
from (select count(*) n from {{ ref('fct_enrollments') }}) mart
cross join (select count(*) n from {{ ref('stg_crm__enrollments') }} where not is_quarantined and not is_deleted) stg
where mart.n <> stg.n
union all
select 'fct_progress_events', mart.n, stg.n
from (select count(*) n from {{ ref('fct_progress_events') }}) mart
cross join (select count(*) n from {{ ref('stg_lxp__progress_events') }} where not is_quarantined and not is_deleted) stg
where mart.n <> stg.n
union all
select 'fct_survey_responses', mart.n, stg.n
from (select count(*) n from {{ ref('fct_survey_responses') }}) mart
cross join (select count(*) n from {{ ref('stg_lxp__survey_responses') }} where not is_quarantined and not is_deleted) stg
where mart.n <> stg.n
union all
select 'fct_coach_assignments', mart.n, stg.n
from (select count(*) n from {{ ref('fct_coach_assignments') }}) mart
cross join (select count(*) n from {{ ref('stg_coach__assignments') }}) stg
where mart.n <> stg.n
union all
select 'fct_enrollment_progress', mart.n, stg.n
from (select count(*) n from {{ ref('fct_enrollment_progress') }}) mart
cross join (select count(*) n from {{ ref('stg_crm__enrollments') }} where not is_quarantined and not is_deleted) stg
where mart.n <> stg.n
