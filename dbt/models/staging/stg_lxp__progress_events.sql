{{ config(
    materialized='incremental',
    unique_key='event_id',
    incremental_strategy='delete+insert'
) }}

-- Incremental: a full build reads every curated table (one per snapshot date); later runs only read
-- the newest day(s) (macro incremental_snapshot_filter), de-duplicate that batch and replace the rows
-- of the same event_id. `dbt build --full-refresh` rebuilds from the complete history.
with source as (

    select * from {{ curated_source('progress_events') }}
    where {{ incremental_snapshot_filter() }}

),

typed as (

    select
        cast(event_id as varchar) as event_id,
        cast(enrollment_id as varchar) as enrollment_id,
        cast(module_id as varchar) as module_id,
        cast(event_type as varchar) as event_type,
        cast(is_event_type_valid as boolean) as is_event_type_valid,
        cast(has_enrollment_reference as boolean) as has_enrollment_reference,
        cast(has_module_reference as boolean) as has_module_reference,
        cast(enrollment_training_id as varchar) as enrollment_training_id,
        cast(module_training_id as varchar) as module_training_id,
        cast(is_module_in_enrollment_training as boolean) as is_module_in_enrollment_training,
        cast(is_event_before_cohort_start as boolean) as is_event_before_cohort_start,
        cast(event_at_utc as timestamp with time zone) as event_at_utc,
        case when event_at_utc is null then event_time_raw end as event_at_unparsed_raw,
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
        ['event_id'],
        'event_at_utc desc nulls last, ingested_at desc nulls last, run_id desc'
    ) }}

)

select
    deduplicated.event_id,
    deduplicated.enrollment_id,
    deduplicated.module_id,
    deduplicated.event_type,
    deduplicated.is_event_type_valid,
    deduplicated.has_enrollment_reference,
    deduplicated.has_module_reference,
    deduplicated.enrollment_training_id,
    deduplicated.module_training_id,
    deduplicated.is_module_in_enrollment_training,
    deduplicated.is_event_before_cohort_start,
    deduplicated.event_at_utc,
    deduplicated.event_at_unparsed_raw,
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
