-- Per-coach counts cannot exceed the CRM total of active/paused enrollments.
select 'coach_total_exceeds_crm' as problem
from (
    select sum(active_or_paused_enrollment_count) as per_coach_total from {{ ref('rpt_coach_utilization') }}
) coach
cross join {{ ref('rpt_coach_unassigned_enrollments') }} unassigned
where coach.per_coach_total < unassigned.assigned_active_or_paused_enrollment_count
