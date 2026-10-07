select
    event_id,
    enrollment_id,
    module_id,
    event_type,
    event_at_utc,
    has_enrollment_reference,
    has_module_reference,
    is_module_in_enrollment_training,
    is_event_before_cohort_start,
    validation_status,
    quality_flags,
    ingested_at
from {{ ref('stg_lxp__progress_events') }}
where not is_quarantined
  and not is_deleted
