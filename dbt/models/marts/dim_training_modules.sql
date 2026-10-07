select
    training_id,
    module_id,
    module_order,
    module_name,
    estimated_hours,
    source_as_of_date,
    validation_status,
    ingested_at
from {{ ref('stg_lxp__training_modules') }}
where not is_quarantined
  and not is_deleted
