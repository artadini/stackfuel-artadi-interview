{#- One row per enrollment. The participant lookup is many-to-one (participant_id is unique), so
    the grain is preserved. Enrollments of unknown participants stay (funding_type = unknown). -#}

with enrollments as (

    select *
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted

),

participants as (

    select participant_id, funding_type
    from {{ ref('dim_participants') }}

)

select
    enrollments.enrollment_id,
    enrollments.participant_id,
    enrollments.training_id,
    enrollments.cohort_start_date,
    enrollments.cohort_start_date_unparsed_raw,
    enrollments.planned_end_date,
    enrollments.planned_end_date_unparsed_raw,
    enrollments.planned_duration_days,
    enrollments.status,
    enrollments.is_open_status,
    enrollments.status_changed_at_utc,
    enrollments.created_at_utc,
    enrollments.modified_at_utc,
    coalesce(participants.funding_type, 'unknown') as funding_type,
    enrollments.has_participant_reference,
    enrollments.has_training_reference,
    participants.participant_id is not null as is_participant_in_dim,
    enrollments.validation_status,
    enrollments.quality_flags,
    enrollments.ingested_at
from enrollments
left join participants
    on participants.participant_id = enrollments.participant_id
