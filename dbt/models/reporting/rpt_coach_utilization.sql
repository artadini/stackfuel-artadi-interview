{#- Question 5, per coach, from the latest coach workbook snapshot.

    * active_or_paused_enrollment_count: DISTINCT enrollments (status aktiv or pausiert) that the
      coach is confidently assigned to (row matched to exactly one enrollment).
    * red_enrollment_count: those enrollments for which the coach's row is red.
    * unmatched_enrollment_count: workbook rows of the coach that could not be linked to any
      participant (match_status = unmatched); they cannot be tied to an enrollment status.
    * ambiguous_assignment_count: rows that stay ambiguous (never counted as assigned).

    Snapshot: the workbook's business date is 2026-09-01, the KPI cutoff is 2026-08-31;
    snapshot_mismatch_flag keeps that documented one-day mismatch visible. -#}

with latest_rows as (

    select *
    from {{ ref('fct_coach_assignments') }}
    where is_latest_snapshot

),

per_coach as (

    select
        coach,
        count(distinct case
            when is_confident_assignment and is_active_or_paused then matched_enrollment_id
        end) as active_or_paused_enrollment_count,
        count(distinct case
            when is_confident_assignment and is_active_or_paused and is_red then matched_enrollment_id
        end) as red_enrollment_count,
        count(*) filter (where match_status = 'unmatched') as unmatched_enrollment_count,
        count(*) filter (where match_status = 'ambiguous') as ambiguous_assignment_count,
        count(*) as workbook_row_count,
        any_value(snapshot_date) as coach_snapshot_date,
        any_value(coach_snapshot_as_of_date) as coach_snapshot_as_of_date,
        bool_or(snapshot_mismatch_flag) as snapshot_mismatch_flag
    from latest_rows
    group by coach

)

select
    coach,
    active_or_paused_enrollment_count,
    red_enrollment_count,
    unmatched_enrollment_count,
    ambiguous_assignment_count,
    workbook_row_count,
    {{ reporting_date() }} as reporting_date,
    coach_snapshot_date,
    coach_snapshot_as_of_date,
    snapshot_mismatch_flag
from per_coach
