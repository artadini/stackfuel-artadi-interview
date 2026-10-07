{#- Question 1: active enrollments per training at the reporting date. Counts enrollments, not
    distinct participants. Every catalog training appears, also with 0. -#}

with trainings as (

    select training_id, training_name
    from {{ ref('dim_trainings') }}

),

active as (

    select
        training_id,
        count(*) as active_enrollment_count,
        count(*) filter (where is_status_changed_after_reporting_date) as status_changed_after_cutoff_count,
        count(*) filter (where is_past_planned_end) as past_planned_end_count,
        count(*) filter (where is_cohort_start_after_reporting_date) as cohort_start_after_cutoff_count
    from {{ ref('int_active_enrollments') }}
    group by training_id

)

select
    trainings.training_id,
    trainings.training_name,
    coalesce(active.active_enrollment_count, 0) as active_enrollment_count,
    {{ reporting_date() }} as reporting_date,
    coalesce(active.status_changed_after_cutoff_count, 0) as status_changed_after_cutoff_count,
    coalesce(active.past_planned_end_count, 0) as past_planned_end_count,
    coalesce(active.cohort_start_after_cutoff_count, 0) as cohort_start_after_cutoff_count,
    cast(list_filter(
        [
            'current_status_used_no_status_history',
            case when coalesce(active.status_changed_after_cutoff_count, 0) > 0 then 'status_changed_after_cutoff' end,
            case when coalesce(active.past_planned_end_count, 0) > 0 then 'active_past_planned_end' end,
            case when coalesce(active.cohort_start_after_cutoff_count, 0) > 0 then 'cohort_start_after_cutoff' end
        ],
        flag -> flag is not null
    ) as varchar[]) as data_quality_flags
from trainings
left join active
    on active.training_id = trainings.training_id
