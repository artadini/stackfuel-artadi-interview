-- Q3: Curriculum progress versus elapsed planned time for active enrollments, and the share that is
-- at least 15 percentage points behind schedule (progress_gap <= -0.15). 'ALL' = all trainings.
select
    training_id,
    training_name,
    active_enrollment_count,
    evaluable_active_enrollment_count,
    behind_schedule_count,
    round(behind_schedule_share, 4) as behind_schedule_share,
    round(average_completed_module_share, 4) as average_completed_module_share,
    round(average_time_progress_share, 4) as average_time_progress_share
from {{ ref('rpt_progress_vs_time_summary') }}
order by is_total, training_id
