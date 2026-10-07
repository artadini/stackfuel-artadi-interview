{#- One row per enrollment. Both sources are already one row per enrollment, so the join is
    one-to-one. Time columns exist for active enrollments only. -#}

select
    time_progress.enrollment_id,
    time_progress.training_id,
    time_progress.status,
    time_progress.is_active,
    time_progress.completed_module_count,
    time_progress.total_module_count,
    time_progress.completed_module_share,
    time_progress.elapsed_days,
    time_progress.planned_duration_days,
    time_progress.time_progress_share,
    time_progress.time_progress_quality_flag,
    time_progress.progress_gap,
    time_progress.is_behind_schedule,
    progress.progress_event_count,
    progress.last_event_at_utc,
    time_progress.reporting_date
from {{ ref('int_enrollment_time_progress') }} as time_progress
inner join {{ ref('int_enrollment_progress') }} as progress
    on progress.enrollment_id = time_progress.enrollment_id
