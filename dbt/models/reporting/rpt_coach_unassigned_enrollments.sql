{#- Question 5, summary row (grain: one row): how many active or paused enrollments (CRM, current
    status) have NO confident coach assignment in the latest workbook snapshot. Ambiguous and
    unmatched workbook rows are not treated as assigned, and they are reported separately. -#}

with open_enrollments as (

    select enrollment_id
    from {{ ref('fct_enrollments') }}
    where status in ('aktiv', 'pausiert')

),

latest_rows as (

    select *
    from {{ ref('fct_coach_assignments') }}
    where is_latest_snapshot

),

assigned as (

    -- one row per enrollment, however many workbook rows claim it
    select distinct matched_enrollment_id as enrollment_id
    from latest_rows
    where is_confident_assignment

),

workbook as (

    select
        count(*) filter (where match_status = 'ambiguous') as ambiguous_row_count,
        count(*) filter (where match_status = 'unmatched') as unmatched_row_count,
        any_value(snapshot_date) as coach_snapshot_date,
        any_value(coach_snapshot_as_of_date) as coach_snapshot_as_of_date,
        coalesce(bool_or(snapshot_mismatch_flag), true) as snapshot_mismatch_flag
    from latest_rows

)

select
    {{ reporting_date() }} as reporting_date,
    count(*) as active_or_paused_enrollment_count,
    count(*) filter (where assigned.enrollment_id is not null) as assigned_active_or_paused_enrollment_count,
    count(*) filter (where assigned.enrollment_id is null) as unassigned_active_or_paused_enrollment_count,
    any_value(workbook.ambiguous_row_count) as ambiguous_workbook_row_count,
    any_value(workbook.unmatched_row_count) as unmatched_workbook_row_count,
    any_value(workbook.coach_snapshot_date) as coach_snapshot_date,
    any_value(workbook.coach_snapshot_as_of_date) as coach_snapshot_as_of_date,
    any_value(workbook.snapshot_mismatch_flag) as snapshot_mismatch_flag
from open_enrollments
left join assigned
    on assigned.enrollment_id = open_enrollments.enrollment_id
cross join workbook
