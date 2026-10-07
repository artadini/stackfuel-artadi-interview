{{ config(
    materialized='incremental',
    unique_key=['coach_assignment_id', 'snapshot_date'],
    incremental_strategy='delete+insert'
) }}

{#- One row per workbook row and snapshot day. Rows are only de-duplicated within the same
    (coach_assignment_id, snapshot_date); different snapshot dates stay separate. The coach
    workbook has no source ID, so coach_assignment_id (SHA-256 of file|snapshot_date|excel_row)
    is the technical key. Free-text notes and e-mail addresses are intentionally not selected.

    Incremental: a full build reads every curated table; later runs only read the newest day(s)
    (macro incremental_snapshot_filter) and replace the rows of the same key. A new workbook
    version is a new snapshot day, so older days are never touched. -#}

with source as (

    select * from {{ curated_source('coach_betreuungsliste') }}
    where {{ incremental_snapshot_filter() }}

),

typed as (

    select
        cast(coach_assignment_id as varchar) as coach_assignment_id,
        cast(snapshot_date as date) as snapshot_date,
        cast(excel_row_number as bigint) as excel_row_number,
        cast(coach as varchar) as coach,
        cast(training_id as varchar) as training_id,
        cast(training_match_status as varchar) as training_match_status,
        -- raw text only where no training could be derived from it
        case when training_id is null then training_raw end as training_unmapped_raw,
        cast(cohort_start_date as date) as cohort_start_date,
        case when cohort_start_date is null then kohorte_raw end as cohort_start_date_unparsed_raw,
        cast(ampel as varchar) as ampel,
        cast(ampel_status as varchar) as ampel_status,
        case when ampel = 'unknown' and ampel_status = 'unmapped' then ampel_raw end as ampel_unmapped_raw,
        cast(last_contact_date as date) as last_contact_date,
        case when last_contact_date is null then letzter_kontakt_raw end as last_contact_date_unparsed_raw,
        cast(matched_participant_id as varchar) as matched_participant_id,
        cast(matched_enrollment_id as varchar) as matched_enrollment_id,
        cast(match_status as varchar) as match_status,
        cast(match_method as varchar) as match_method,
        cast(candidate_count as integer) as candidate_count,
        cast(enrollment_candidate_count as integer) as enrollment_candidate_count,
        cast(enrollment_match_status as varchar) as enrollment_match_status,
        cast(coach_assignment_business_key as varchar) as coach_assignment_business_key,
        cast(is_business_key_duplicate as boolean) as business_key_duplicate_flag,
        cast(has_complete_business_key as boolean) as has_complete_business_key,
        cast(completeness_status as varchar) as completeness_status,
        cast(is_incomplete as boolean) as is_incomplete,
        cast(raw_run_id as varchar) as run_id,
        cast(raw_ingested_at as timestamp with time zone) as ingested_at,
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
        ['coach_assignment_id', 'snapshot_date'],
        'ingested_at desc nulls last, run_id desc'
    ) }}

)

select
    coach_assignment_id,
    snapshot_date,
    excel_row_number,
    coach,
    training_id,
    training_match_status,
    training_unmapped_raw,
    cohort_start_date,
    cohort_start_date_unparsed_raw,
    ampel,
    ampel_status,
    ampel_unmapped_raw,
    last_contact_date,
    last_contact_date_unparsed_raw,
    matched_participant_id,
    matched_enrollment_id,
    match_status,
    match_method,
    candidate_count,
    enrollment_candidate_count,
    enrollment_match_status,
    coach_assignment_business_key,
    business_key_duplicate_flag,
    has_complete_business_key,
    completeness_status,
    is_incomplete,
    run_id,
    ingested_at,
    source_as_of_date,
    quality_flags,
    validation_status,
    is_quarantined
from deduplicated
