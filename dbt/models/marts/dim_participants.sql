{#- Dashboard-facing: no e-mail address and no birth date. One row per source participant_id;
    participants are NOT merged by e-mail (a shared e-mail is a hint, not proof of identity), the
    hint is exposed as canonical_participant_id / has_duplicate_identity. -#}

select
    participant_id,
    canonical_participant_id,
    canonical_participant_id <> participant_id as has_duplicate_identity,
    first_name,
    last_name,
    city,
    federal_state,
    funding_type,
    acquisition_channel,
    is_email_valid,
    is_birth_date_valid,
    created_at_utc,
    modified_at_utc,
    validation_status,
    quality_flags,
    ingested_at
from {{ ref('stg_crm__participants') }}
where not is_quarantined
  and not is_deleted
