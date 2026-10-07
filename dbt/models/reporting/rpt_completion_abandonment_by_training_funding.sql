{#- Question 2: completion and abandonment rates for enrollments whose planned end lies before the
    reporting date, per training and funding type. The denominator is the whole eligible
    population; statuses other than abgeschlossen/abgebrochen are shown as other_status_count and
    stay in the denominator (unresolved enrollments are not silently dropped). -#}

with trainings as (

    select training_id, training_name
    from {{ ref('dim_trainings') }}

),

eligible as (

    select
        training_id,
        funding_type,
        count(*) as eligible_enrollment_count,
        count(*) filter (where is_completed) as completed_count,
        count(*) filter (where is_abandoned) as abandoned_count,
        count(*) filter (where is_other_status) as other_status_count,
        count(*) filter (where is_participant_missing) as missing_participant_count
    from {{ ref('int_completed_enrollments') }}
    group by training_id, funding_type

)

select
    eligible.training_id,
    trainings.training_name,
    eligible.funding_type,
    eligible.eligible_enrollment_count,
    eligible.completed_count,
    eligible.abandoned_count,
    eligible.other_status_count,
    eligible.completed_count * 1.0 / eligible.eligible_enrollment_count as completed_share,
    eligible.abandoned_count * 1.0 / eligible.eligible_enrollment_count as abandoned_share,
    eligible.other_status_count * 1.0 / eligible.eligible_enrollment_count as other_status_share,
    eligible.missing_participant_count,
    {{ reporting_date() }} as reporting_date
from eligible
left join trainings
    on trainings.training_id = eligible.training_id
