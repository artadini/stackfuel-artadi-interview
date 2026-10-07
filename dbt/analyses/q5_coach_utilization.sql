-- Q5: Active or paused enrollments per coach (latest coach snapshot), how many are red,
-- and how many active/paused enrollments could not be assigned to any coach.
select
    coach,
    active_or_paused_enrollment_count,
    red_enrollment_count,
    unmatched_enrollment_count,
    ambiguous_assignment_count,
    reporting_date,
    coach_snapshot_as_of_date,
    snapshot_mismatch_flag
from {{ ref('rpt_coach_utilization') }}
union all by name
select
    '(unassigned active/paused enrollments)' as coach,
    unassigned_active_or_paused_enrollment_count as active_or_paused_enrollment_count,
    cast(null as bigint) as red_enrollment_count,
    unmatched_workbook_row_count as unmatched_enrollment_count,
    ambiguous_workbook_row_count as ambiguous_assignment_count,
    reporting_date,
    coach_snapshot_as_of_date,
    snapshot_mismatch_flag
from {{ ref('rpt_coach_unassigned_enrollments') }}
order by 1
