{#- One row per enrollment. Time progress is only calculated for active enrollments (status =
    'aktiv'); for every other status the time columns are NULL. -#}

with enrollments as (

    select
        enrollment_id,
        participant_id,
        training_id,
        status,
        cohort_start_date,
        planned_end_date,
        status_changed_at_utc
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted

),

progress as (

    select
        enrollment_id,
        total_module_count,
        completed_module_count,
        completed_module_share
    from {{ ref('int_enrollment_progress') }}

),

timed as (

    select
        enrollments.enrollment_id,
        enrollments.training_id,
        enrollments.status,
        enrollments.cohort_start_date,
        enrollments.planned_end_date,
        enrollments.status = 'aktiv' as is_active,
        case when enrollments.status = 'aktiv'
            then date_diff('day', enrollments.cohort_start_date, {{ reporting_date() }})
        end as elapsed_days,
        case when enrollments.status = 'aktiv'
            then date_diff('day', enrollments.cohort_start_date, enrollments.planned_end_date)
        end as planned_duration_days,
        progress.total_module_count,
        progress.completed_module_count,
        progress.completed_module_share
    from enrollments
    left join progress
        on progress.enrollment_id = enrollments.enrollment_id

),

shares as (

    select
        *,
        case
            when not is_active then null
            when cohort_start_date is null or planned_end_date is null then 'missing_dates'
            when planned_duration_days <= 0 then 'non_positive_planned_duration'
            when elapsed_days < 0 then 'cohort_not_started'
            when elapsed_days > planned_duration_days then 'past_planned_end'
            else 'ok'
        end as time_progress_quality_flag,
        case
            when not is_active
                or cohort_start_date is null
                or planned_end_date is null
                or planned_duration_days <= 0 then null
            -- ratio clamped to 0..1 (not started = 0, past planned end = 1); DECIMAL(9,6) keeps
            -- averages exactly reproducible (DOUBLE sums depend on thread scheduling)
            else cast(
                least(1.0, greatest(0.0, elapsed_days * 1.0 / planned_duration_days)) as decimal(9, 6)
            )
        end as time_progress_share
    from timed

)

select
    enrollment_id,
    training_id,
    status,
    is_active,
    cohort_start_date,
    planned_end_date,
    elapsed_days,
    planned_duration_days,
    time_progress_share,
    time_progress_quality_flag,
    total_module_count,
    completed_module_count,
    completed_module_share,
    completed_module_share - time_progress_share as progress_gap,
    -- >= 15 percentage points behind: curriculum progress is at least 0.15 lower than time progress.
    -- Both shares are exact decimals, so a gap of exactly -0.15 is classified as behind.
    case
        when completed_module_share is null or time_progress_share is null then null
        else completed_module_share - time_progress_share <= {{ var('behind_schedule_threshold') }}
    end as is_behind_schedule,
    {{ reporting_date() }} as reporting_date
from shares
