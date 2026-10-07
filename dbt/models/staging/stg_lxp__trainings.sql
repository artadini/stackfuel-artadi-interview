{{ config(
    materialized='incremental',
    unique_key='training_id',
    incremental_strategy='delete+insert'
) }}

-- Incremental: a full build reads every curated table (one per snapshot date); later runs only read
-- the newest day(s) (macro incremental_snapshot_filter), de-duplicate that batch and replace the rows
-- of the same training_id. `dbt build --full-refresh` rebuilds from the complete history.
with source as (

    select * from {{ curated_source('trainings') }}
    where {{ incremental_snapshot_filter() }}

),

typed as (

    select
        cast(training_id as varchar) as training_id,
        cast(training_name as varchar) as training_name,
        cast(track as varchar) as track,
        cast(variant as varchar) as variant,
        cast(duration_weeks as integer) as duration_weeks,
        cast(list_price_eur as decimal(12, 2)) as list_price_eur,
        cast(is_active as boolean) as is_active,
        cast(has_consistent_modules as boolean) as has_consistent_modules,
        cast(payload_json as json) as payload_json,
        cast(source_as_of_date as date) as source_as_of_date,
        cast(raw_run_id as varchar) as run_id,
        cast(raw_ingested_at as timestamp with time zone) as ingested_at,
        cast(snapshot_date as date) as snapshot_date,
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
        ['training_id'],
        'source_as_of_date desc nulls last, ingested_at desc nulls last, run_id desc'
    ) }}

)

select
    deduplicated.training_id,
    deduplicated.training_name,
    deduplicated.track,
    deduplicated.variant,
    deduplicated.duration_weeks,
    deduplicated.list_price_eur,
    deduplicated.is_active,
    deduplicated.has_consistent_modules,
    deduplicated.payload_json,
    deduplicated.source_as_of_date,
    deduplicated.run_id,
    deduplicated.ingested_at,
    deduplicated.snapshot_date,
    deduplicated.quality_flags,
    deduplicated.validation_status,
    deduplicated.is_quarantined,
    false as is_deleted  -- hard deletes at the source are not detected by the pipeline
from deduplicated
