{{ config(
    materialized='incremental',
    unique_key='training_id',
    incremental_strategy='delete+insert'
) }}

{#- Flattening happens here, in staging. The raw layer keeps the nested `modules` array untouched
    inside payload_json. One row per module: no module_1_name style columns.

    Incremental: only trainings whose current version is new (snapshot_date window, like the other
    staging models) are flattened again. The unique key is the *training*, not the module: all old
    module rows of such a training are deleted first, so a module removed from the catalog
    disappears instead of staying behind. -#}

with trainings as (

    select
        training_id,
        payload_json,
        source_as_of_date,
        run_id,
        ingested_at,
        snapshot_date,
        quality_flags,
        validation_status,
        is_quarantined,
        is_deleted
    from {{ ref('stg_lxp__trainings') }}
    where {{ incremental_snapshot_filter() }}

),

unnested as (

    select
        trainings.*,
        unnest(cast(json_extract(trainings.payload_json, '$.modules') as json[])) as module
    from trainings

),

typed as (

    select
        training_id,
        cast(json_extract_string(module, '$.module_id') as varchar) as module_id,
        cast(json_extract_string(module, '$.module_order') as integer) as module_order,
        cast(json_extract_string(module, '$.module_name') as varchar) as module_name,
        cast(json_extract_string(module, '$.estimated_hours') as integer) as estimated_hours,
        source_as_of_date,
        run_id,
        ingested_at,
        snapshot_date,
        quality_flags,
        validation_status,
        is_quarantined,
        is_deleted,
        -- lineage helpers for the de-duplication macro
        cast(null as varchar) as payload_hash,
        cast(null as bigint) as source_row_number
    from unnested

),

deduplicated as (

    -- The parent is already one row per training; this only guards against a module ID that
    -- occurs twice inside one payload (the curated flag has_consistent_modules reports it).
    {{ dedupe_latest(
        'typed',
        ['training_id', 'module_id'],
        'module_order asc nulls last, module_name asc nulls last'
    ) }}

)

select
    training_id,
    module_id,
    module_order,
    module_name,
    estimated_hours,
    source_as_of_date,
    run_id,
    ingested_at,
    snapshot_date,
    quality_flags,
    validation_status,
    is_quarantined,
    is_deleted
from deduplicated
