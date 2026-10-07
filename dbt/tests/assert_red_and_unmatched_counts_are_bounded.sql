-- red <= total, unassigned <= active/paused, unmatched rows <= workbook rows; no negative counts.
select 'rpt_coach_utilization' as model_name, coach as key_value
from {{ ref('rpt_coach_utilization') }}
where red_enrollment_count > active_or_paused_enrollment_count
   or unmatched_enrollment_count > workbook_row_count
   or active_or_paused_enrollment_count < 0
union all
select 'rpt_coach_unassigned_enrollments', cast(reporting_date as varchar)
from {{ ref('rpt_coach_unassigned_enrollments') }}
where unassigned_active_or_paused_enrollment_count > active_or_paused_enrollment_count
   or assigned_active_or_paused_enrollment_count + unassigned_active_or_paused_enrollment_count
        <> active_or_paused_enrollment_count
