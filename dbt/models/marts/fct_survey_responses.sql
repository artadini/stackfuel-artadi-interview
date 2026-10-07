{#- One row per response. training_id comes from the enrollment (many-to-one lookup). Free-text
    answers are not passed on: only the numeric score and its validity. -#}

with responses as (

    select *
    from {{ ref('stg_lxp__survey_responses') }}
    where not is_quarantined
      and not is_deleted

),

enrollments as (

    select enrollment_id, training_id
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted

)

select
    responses.response_id,
    responses.enrollment_id,
    enrollments.training_id,
    responses.survey_type,
    responses.survey_week,
    responses.question_key,
    responses.answer_score,
    responses.answer_context,
    responses.nps_category,
    responses.submitted_at_utc,
    responses.is_answer_missing,
    responses.is_answer_valid,
    responses.is_question_valid_for_survey_type,
    responses.is_survey_week_valid,
    responses.has_enrollment_reference,
    enrollments.enrollment_id is not null as is_enrollment_in_fact,
    responses.validation_status,
    responses.quality_flags,
    responses.ingested_at
from responses
left join enrollments
    on enrollments.enrollment_id = responses.enrollment_id
