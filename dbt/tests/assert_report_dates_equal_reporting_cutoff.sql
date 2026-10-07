-- Every report row is evaluated at the documented cutoff (var reporting_date = 2026-08-31).
select 'rpt_active_enrollments_by_training' as model_name, count(*) as bad_rows
from {{ ref('rpt_active_enrollments_by_training') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0
union all
select 'rpt_completion_abandonment_by_training_funding', count(*)
from {{ ref('rpt_completion_abandonment_by_training_funding') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0
union all
select 'rpt_progress_vs_time', count(*)
from {{ ref('rpt_progress_vs_time') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0
union all
select 'rpt_progress_vs_time_summary', count(*)
from {{ ref('rpt_progress_vs_time_summary') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0
union all
select 'rpt_nps_by_training_quarter', count(*)
from {{ ref('rpt_nps_by_training_quarter') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0
union all
select 'rpt_coach_utilization', count(*)
from {{ ref('rpt_coach_utilization') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0
union all
select 'rpt_coach_unassigned_enrollments', count(*)
from {{ ref('rpt_coach_unassigned_enrollments') }} where reporting_date is distinct from {{ reporting_date() }}
having count(*) > 0

