{{ config(
    materialized='incremental',
    unique_key='enrollment_id',
    incremental_strategy='delete+insert'
) }}

-- Incremental: a full build reads every curated table (one per snapshot date); later runs only read
-- the newest day(s) (macro incremental_snapshot_filter), de-duplicate that batch and replace the rows
-- of the same enrollment_id. `dbt build --full-refresh` rebuilds from the complete history.
with source as (

    select * from {{ curated_source('enrollments') }}
    where {{ incremental_snapshot_filter() }}

),

typed as (

    select
        cast(enrollment_id as varchar) as enrollment_id,
        cast(participant_id as varchar) as participant_id,
        cast(training_id as varchar) as training_id,
        cast(cohort_start_date as date) as cohort_start_date,
        case when cohort_start_date is null then cohort_start_date_raw end as cohort_start_date_unparsed_raw,
        cast(planned_end_date as date) as planned_end_date,
        case when planned_end_date is null then planned_end_date_raw end as planned_end_date_unparsed_raw,
        cast(planned_duration_days as integer) as planned_duration_days,
        cast(status as varchar) as status,
        cast(is_status_valid as boolean) as is_status_valid,
        cast(is_open_status as boolean) as is_open_status,
        cast(has_participant_reference as boolean) as has_participant_reference,
        cast(has_training_reference as boolean) as has_training_reference,
        cast(status_changed_at as timestamp) as status_changed_at,
        cast(status_changed_at_utc as timestamp with time zone) as status_changed_at_utc,
        case when status_changed_at_utc is null then status_changed_at_raw end as status_changed_at_unparsed_raw,
        cast(created_at as timestamp) as created_at,
        cast(created_at_utc as timestamp with time zone) as created_at_utc,
        case when created_at_utc is null then created_at_raw end as created_at_unparsed_raw,
        cast(modified_at as timestamp) as modified_at,
        cast(modified_at_utc as timestamp with time zone) as modified_at_utc,
        case when modified_at_utc is null then modified_at_raw end as modified_at_unparsed_raw,
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
        ['enrollment_id'],
        'modified_at_utc desc nulls last, ingested_at desc nulls last, run_id desc'
    ) }}

)

select
    deduplicated.enrollment_id,
    deduplicated.participant_id,
    deduplicated.training_id,
    deduplicated.cohort_start_date,
    deduplicated.cohort_start_date_unparsed_raw,
    deduplicated.planned_end_date,
    deduplicated.planned_end_date_unparsed_raw,
    deduplicated.planned_duration_days,
    deduplicated.status,
    deduplicated.is_status_valid,
    deduplicated.is_open_status,
    deduplicated.has_participant_reference,
    deduplicated.has_training_reference,
    deduplicated.status_changed_at,
    deduplicated.status_changed_at_utc,
    deduplicated.status_changed_at_unparsed_raw,
    deduplicated.created_at,
    deduplicated.created_at_utc,
    deduplicated.created_at_unparsed_raw,
    deduplicated.modified_at,
    deduplicated.modified_at_utc,
    deduplicated.modified_at_unparsed_raw,
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
