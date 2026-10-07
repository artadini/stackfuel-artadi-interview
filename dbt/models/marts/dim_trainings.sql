select
    training_id,
    training_name,
    track,
    variant,
    duration_weeks,
    list_price_eur,
    is_active,
    source_as_of_date,
    has_consistent_modules,
    validation_status,
    quality_flags,
    ingested_at
from {{ ref('stg_lxp__trainings') }}
where not is_quarantined
  and not is_deleted
