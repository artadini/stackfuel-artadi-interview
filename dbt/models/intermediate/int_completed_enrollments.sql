{#- One row per enrollment whose planned end lies before the reporting date (the eligible
    population for the completion/abandonment rates) with its outcome. Statuses other than
    abgeschlossen/abgebrochen stay in the population as `other` (unresolved), they are not dropped.

    The participant join is many-to-one (participant_id is unique in staging). -#}

with enrollments as (

    select enrollment_id, participant_id, training_id, status, planned_end_date
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted
      and planned_end_date < {{ reporting_date() }}

),

participants as (

    select participant_id, funding_type
    from {{ ref('stg_crm__participants') }}
    where not is_deleted

)

select
    enrollments.enrollment_id,
    enrollments.participant_id,
    enrollments.training_id,
    -- enrollments without a known participant have no funding type (reported as unknown)
    coalesce(participants.funding_type, 'unknown') as funding_type,
    enrollments.status,
    enrollments.planned_end_date,
    enrollments.status = 'abgeschlossen' as is_completed,
    enrollments.status = 'abgebrochen' as is_abandoned,
    enrollments.status not in ('abgeschlossen', 'abgebrochen') as is_other_status,
    participants.participant_id is null as is_participant_missing,
    {{ reporting_date() }} as reporting_date
from enrollments
left join participants
    on participants.participant_id = enrollments.participant_id
