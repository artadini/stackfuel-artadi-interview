{{ config(
    materialized='incremental',
    unique_key='participant_id',
    incremental_strategy='delete+insert'
) }}

-- Incremental: a full build reads every curated table (one per snapshot date); later runs only read
-- the newest day(s) (macro incremental_snapshot_filter), de-duplicate that batch and replace the rows
-- of the same participant_id. `dbt build --full-refresh` rebuilds from the complete history.
with source as (

    select * from {{ curated_source('participants') }}
    where {{ incremental_snapshot_filter() }}

),

typed as (

    select
        cast(participant_id as varchar) as participant_id,
        cast(canonical_participant_id as varchar) as canonical_participant_id,
        cast(first_name as varchar) as first_name,
        cast(last_name as varchar) as last_name,
        cast(name_match_key as varchar) as name_match_key,
        cast(email as varchar) as email,
        cast(is_email_valid as boolean) as is_email_valid,
        cast(birth_date as date) as birth_date,
        -- raw text only where the clean value is missing although the source had a value
        case when birth_date is null then birth_date_raw end as birth_date_unparsed_raw,
        cast(is_birth_date_valid as boolean) as is_birth_date_valid,
        cast(city as varchar) as city,
        cast(federal_state as varchar) as federal_state,
        cast(funding_type as varchar) as funding_type,
        cast(acquisition_channel as varchar) as acquisition_channel,
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
        ['participant_id'],
        'modified_at_utc desc nulls last, ingested_at desc nulls last, run_id desc'
    ) }}

)

select
    deduplicated.participant_id,
    deduplicated.canonical_participant_id,
    deduplicated.first_name,
    deduplicated.last_name,
    deduplicated.name_match_key,
    deduplicated.email,
    deduplicated.is_email_valid,
    deduplicated.birth_date,
    deduplicated.birth_date_unparsed_raw,
    deduplicated.is_birth_date_valid,
    deduplicated.city,
    deduplicated.federal_state,
    deduplicated.funding_type,
    deduplicated.acquisition_channel,
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
