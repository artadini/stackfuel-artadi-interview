{#- One row per workbook row and snapshot day. Matching was done by the pipeline (e-mail, then name
    + training + cohort); here the match is only *validated* against the enrollments, never
    forced: ambiguous and unmatched rows keep their status and get no enrollment.

    The join to enrollments is many-to-one (a row has at most one matched_enrollment_id and the
    enrollment ID is unique), so the grain is preserved. -#}

with assignments as (

    select * from {{ ref('stg_coach__assignments') }}

),

latest as (

    -- The workbook is a manually maintained snapshot: the newest snapshot day that has rows is
    -- the "latest valid snapshot". Note: snapshot_date is the pipeline day, not a business date.
    select max(snapshot_date) as latest_snapshot_date
    from assignments

),

enrollments as (

    select enrollment_id, training_id, status
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted

),

matched as (

    select
        assignments.*,
        enrollments.status as enrollment_status,
        enrollments.training_id as enrollment_training_id,
        assignments.match_status = 'matched' and enrollments.enrollment_id is not null as is_confident_assignment
    from assignments
    left join enrollments
        on assignments.match_status = 'matched'
        and enrollments.enrollment_id = assignments.matched_enrollment_id

),

flagged as (

    select
        matched.*,
        coalesce(matched.enrollment_status in ('aktiv', 'pausiert'), false) as is_active_or_paused,
        matched.ampel = 'red' as is_red,
        matched.snapshot_date = latest.latest_snapshot_date as is_latest_snapshot,
        latest.latest_snapshot_date,
        -- rows of the same snapshot that claim the same enrollment (25 enrollments in the test data)
        count(*) filter (where matched.is_confident_assignment)
            over (partition by matched.snapshot_date, matched.matched_enrollment_id) as assignment_rows_for_enrollment,
        min(matched.coach) filter (where matched.is_confident_assignment)
            over (partition by matched.snapshot_date, matched.matched_enrollment_id) as first_coach_for_enrollment,
        max(matched.coach) filter (where matched.is_confident_assignment)
            over (partition by matched.snapshot_date, matched.matched_enrollment_id) as last_coach_for_enrollment
    from matched
    cross join latest

)

select
    coach_assignment_id,
    snapshot_date,
    coach,
    training_id,
    cohort_start_date,
    ampel,
    last_contact_date,
    match_status,
    match_method,
    matched_participant_id,
    case when is_confident_assignment then matched_enrollment_id end as matched_enrollment_id,
    is_confident_assignment,
    enrollment_status,
    enrollment_training_id,
    is_active_or_paused,
    is_red,
    business_key_duplicate_flag,
    coalesce(assignment_rows_for_enrollment, 0) as assignment_rows_for_enrollment,
    -- true when different coaches claim the same enrollment in the same snapshot
    coalesce(first_coach_for_enrollment <> last_coach_for_enrollment, false) as has_conflicting_coach,
    is_latest_snapshot,
    latest_snapshot_date,
    source_as_of_date as coach_snapshot_as_of_date,
    -- the workbook is dated 2026-09-01, the KPI cutoff is 2026-08-31 (documented one-day mismatch)
    source_as_of_date is distinct from {{ reporting_date() }} as snapshot_mismatch_flag,
    {{ reporting_date() }} as reporting_date,
    run_id,
    ingested_at,
    quality_flags,
    validation_status,
    is_quarantined
from flagged
