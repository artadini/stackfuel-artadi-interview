-- Enrollment-level marts and reports must have exactly one row per enrollment_id.
select 'fct_enrollments' as model_name, enrollment_id, count(*) as row_count
from {{ ref('fct_enrollments') }} group by enrollment_id having count(*) > 1
union all
select 'fct_enrollment_progress', enrollment_id, count(*)
from {{ ref('fct_enrollment_progress') }} group by enrollment_id having count(*) > 1
union all
select 'rpt_progress_vs_time', enrollment_id, count(*)
from {{ ref('rpt_progress_vs_time') }} group by enrollment_id having count(*) > 1
union all
select 'int_enrollment_progress', enrollment_id, count(*)
from {{ ref('int_enrollment_progress') }} group by enrollment_id having count(*) > 1
