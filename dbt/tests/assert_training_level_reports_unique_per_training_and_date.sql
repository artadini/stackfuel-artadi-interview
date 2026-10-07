-- Training-level reports: no duplicate (training_id, reporting_date).
select 'rpt_active_enrollments_by_training', training_id, reporting_date, count(*)
from {{ ref('rpt_active_enrollments_by_training') }} group by training_id, reporting_date having count(*) > 1
union all
select 'rpt_progress_vs_time_summary', training_id, reporting_date, count(*)
from {{ ref('rpt_progress_vs_time_summary') }} group by training_id, reporting_date having count(*) > 1
