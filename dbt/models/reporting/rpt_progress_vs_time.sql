{#- Question 3, detail: one row per active enrollment (status aktiv) with curriculum progress,
    elapsed planned time and the gap. Behind schedule = progress_gap <= -0.15. -#}

select
    progress.enrollment_id,
    progress.training_id,
    trainings.training_name,
    progress.completed_module_count,
    progress.total_module_count,
    progress.completed_module_share,
    progress.elapsed_days,
    progress.planned_duration_days,
    progress.time_progress_share,
    progress.time_progress_quality_flag,
    progress.progress_gap,
    progress.is_behind_schedule,
    progress.reporting_date
from {{ ref('fct_enrollment_progress') }} as progress
left join {{ ref('dim_trainings') }} as trainings
    on trainings.training_id = progress.training_id
where progress.is_active
