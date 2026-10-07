{#- Question 3, aggregate: one row per training plus one overall row (training_id = 'ALL').
    behind_schedule_share is measured on the active enrollments whose progress gap can be
    computed (evaluable_active_enrollment_count). -#}

with detail as (

    select * from {{ ref('rpt_progress_vs_time') }}

),

per_training as (

    select
        training_id,
        any_value(training_name) as training_name,
        count(*) as active_enrollment_count,
        count(*) filter (where progress_gap is not null) as evaluable_active_enrollment_count,
        count(*) filter (where is_behind_schedule) as behind_schedule_count,
        avg(completed_module_share) as average_completed_module_share,
        avg(time_progress_share) as average_time_progress_share
    from detail
    group by training_id

),

overall as (

    select
        'ALL' as training_id,
        'All trainings' as training_name,
        count(*) as active_enrollment_count,
        count(*) filter (where progress_gap is not null) as evaluable_active_enrollment_count,
        count(*) filter (where is_behind_schedule) as behind_schedule_count,
        avg(completed_module_share) as average_completed_module_share,
        avg(time_progress_share) as average_time_progress_share
    from detail

),

combined as (

    select *, false as is_total from per_training
    union all
    select *, true as is_total from overall

)

select
    training_id,
    training_name,
    is_total,
    active_enrollment_count,
    evaluable_active_enrollment_count,
    behind_schedule_count,
    case
        when evaluable_active_enrollment_count = 0 then null
        else behind_schedule_count * 1.0 / evaluable_active_enrollment_count
    end as behind_schedule_share,
    average_completed_module_share,
    average_time_progress_share,
    {{ reporting_date() }} as reporting_date
from combined
