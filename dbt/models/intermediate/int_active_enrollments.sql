{#- One row per active enrollment (status = 'aktiv'). Only the current CRM status exists, so the
    state "at" the reporting date is approximated by the current status; the flags below show
    which active enrollments could be affected. -#}

select
    enrollment_id,
    participant_id,
    training_id,
    cohort_start_date,
    planned_end_date,
    status_changed_at_utc,
    {{ reporting_date() }} as reporting_date,
    -- the status was set after the cutoff, so the status at the cutoff may have been different
    coalesce({{ utc_date('status_changed_at_utc') }} > {{ reporting_date() }}, false) as is_status_changed_after_reporting_date,
    -- the cohort starts after the cutoff: "active" before the start is questionable
    coalesce(cohort_start_date > {{ reporting_date() }}, false) as is_cohort_start_after_reporting_date,
    -- still active although the planned end is before the cutoff (overdue)
    coalesce(planned_end_date < {{ reporting_date() }}, false) as is_past_planned_end
from {{ ref('stg_crm__enrollments') }}
where not is_quarantined
  and not is_deleted
  and status = 'aktiv'
