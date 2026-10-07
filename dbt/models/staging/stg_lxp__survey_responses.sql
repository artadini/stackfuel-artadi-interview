{{ config(
    materialized='incremental',
    unique_key='response_id',
    incremental_strategy='delete+insert'
) }}

-- Incremental: a full build reads every curated table (one per snapshot date); later runs only read
-- the newest day(s) (macro incremental_snapshot_filter), de-duplicate that batch and replace the rows
-- of the same response_id. `dbt build --full-refresh` rebuilds from the complete history.
with source as (

    select * from {{ curated_source('survey_responses') }}
    where {{ incremental_snapshot_filter() }}

),

typed as (

    select
        cast(response_id as varchar) as response_id,
        cast(enrollment_id as varchar) as enrollment_id,
        cast(survey_type as varchar) as survey_type,
        cast(survey_week as integer) as survey_week,
        cast(question_key as varchar) as question_key,
        cast(answer_value as varchar) as answer_value,
        cast(answer_score as decimal(5, 2)) as answer_score,
        cast(answer_context as varchar) as answer_context,
        cast(is_answer_missing as boolean) as is_answer_missing,
        cast(is_answer_valid as boolean) as is_answer_valid,
        cast(nps_category as varchar) as nps_category,
        cast(is_question_valid_for_survey_type as boolean) as is_question_valid_for_survey_type,
        cast(is_survey_week_valid as boolean) as is_survey_week_valid,
        cast(has_enrollment_reference as boolean) as has_enrollment_reference,
        cast(submitted_at_utc as timestamp with time zone) as submitted_at_utc,
        case when submitted_at_utc is null then submitted_at_raw end as submitted_at_unparsed_raw,
        cast(source_timezone as varchar) as source_timezone,
        cast(raw_run_id as varchar) as run_id,
        cast(raw_ingested_at as timestamp with time zone) as ingested_at,
        cast(snapshot_date as date) as snapshot_date,
        cast(source_as_of_date as date) as source_as_of_date,
        cast(source_row_number as bigint) as source_row_number,
        cast(payload_hash as varchar) as payload_hash,
        quality_flags,
        cast(validation_status as varchar) as validation_status,
        cast(is_quarantined as boolean) as is_quarantined
    from source

),

deduplicated as (

    {{ dedupe_latest(
        'typed',
        ['response_id'],
        'submitted_at_utc desc nulls last, ingested_at desc nulls last, run_id desc'
    ) }}

)

select
    deduplicated.response_id,
    deduplicated.enrollment_id,
    deduplicated.survey_type,
    deduplicated.survey_week,
    deduplicated.question_key,
    deduplicated.answer_value,
    deduplicated.answer_score,
    deduplicated.answer_context,
    deduplicated.is_answer_missing,
    deduplicated.is_answer_valid,
    deduplicated.nps_category,
    deduplicated.is_question_valid_for_survey_type,
    deduplicated.is_survey_week_valid,
    deduplicated.has_enrollment_reference,
    deduplicated.submitted_at_utc,
    deduplicated.submitted_at_unparsed_raw,
    deduplicated.source_timezone,
    deduplicated.run_id,
    deduplicated.ingested_at,
    deduplicated.snapshot_date,
    deduplicated.source_as_of_date,
    deduplicated.quality_flags,
    deduplicated.validation_status,
    deduplicated.is_quarantined,
    false as is_deleted  -- hard deletes at the source are not detected by the pipeline
from deduplicated
