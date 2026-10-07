{#- One row per training and feedback quarter. Feedback is aggregated here, before it meets any
    other training-level model. Only completion feedback (abschlussfeedback), the NPS question,
    valid whole-number scores 0..10, submitted up to the reporting date, with a known enrollment
    and training. The quarter is the UTC quarter of submitted_at_utc. -#}

with responses as (

    select response_id, enrollment_id, answer_score, submitted_at_utc
    from {{ ref('stg_lxp__survey_responses') }}
    where not is_quarantined
      and not is_deleted
      and survey_type = 'abschlussfeedback'
      and question_key = 'nps'
      and is_answer_valid
      and answer_score between 0 and 10
      and answer_score = round(answer_score)
      and submitted_at_utc is not null
      and {{ utc_date('submitted_at_utc') }} <= {{ reporting_date() }}

),

enrollments as (

    select enrollment_id, training_id
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted

),

trainings as (

    select training_id
    from {{ ref('stg_lxp__trainings') }}
    where not is_quarantined
      and not is_deleted

),

scored as (

    select
        enrollments.training_id,
        cast(date_trunc('quarter', timezone('UTC', responses.submitted_at_utc)) as date) as feedback_quarter,
        case
            when responses.answer_score >= 9 then 'promoter'
            when responses.answer_score >= 7 then 'passive'
            else 'detractor'
        end as nps_group
    from responses
    inner join enrollments
        on enrollments.enrollment_id = responses.enrollment_id
    inner join trainings
        on trainings.training_id = enrollments.training_id

)

select
    training_id,
    feedback_quarter,
    strftime(feedback_quarter, '%Y') || '-Q' || cast(quarter(feedback_quarter) as varchar) as feedback_quarter_label,
    count(*) as valid_response_count,
    count(*) filter (where nps_group = 'promoter') as promoter_count,
    count(*) filter (where nps_group = 'passive') as passive_count,
    count(*) filter (where nps_group = 'detractor') as detractor_count,
    case
        when count(*) = 0 then null
        else 100.0 * (
            count(*) filter (where nps_group = 'promoter') - count(*) filter (where nps_group = 'detractor')
        ) / count(*)
    end as nps
from scored
group by training_id, feedback_quarter
