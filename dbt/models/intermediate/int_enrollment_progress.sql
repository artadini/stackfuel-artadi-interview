{#- One row per enrollment. Events are aggregated to the enrollment BEFORE they meet any other
    one-to-many relation, and every join below is many-to-one, so rows cannot multiply:
      events (many) -> enrollment (one) -> module of the enrollment's training (one) -#}

with enrollments as (

    select enrollment_id, training_id
    from {{ ref('stg_crm__enrollments') }}
    where not is_quarantined
      and not is_deleted

),

modules as (

    select training_id, module_id
    from {{ ref('stg_lxp__training_modules') }}
    where not is_quarantined
      and not is_deleted

),

module_totals as (

    select training_id, count(*) as total_module_count
    from modules
    group by training_id

),

-- Events up to the reporting date (UTC calendar day), valid records only.
events as (

    select enrollment_id, module_id, event_type, event_at_utc
    from {{ ref('stg_lxp__progress_events') }}
    where not is_quarantined
      and not is_deleted
      and event_at_utc is not null
      and {{ utc_date('event_at_utc') }} <= {{ reporting_date() }}

),

event_aggregates as (

    select
        enrollments.enrollment_id,
        -- distinct completed modules, and only modules that belong to the enrollment's training
        count(distinct case
            when events.event_type = 'module_completed' and modules.module_id is not null
                then events.module_id
        end) as completed_module_count,
        count(*) as event_count,
        min(events.event_at_utc) as first_event_at_utc,
        max(events.event_at_utc) as last_event_at_utc
    from enrollments
    inner join events
        on events.enrollment_id = enrollments.enrollment_id
    left join modules
        on modules.training_id = enrollments.training_id
        and modules.module_id = events.module_id
    group by enrollments.enrollment_id

)

select
    enrollments.enrollment_id,
    enrollments.training_id,
    coalesce(module_totals.total_module_count, 0) as total_module_count,
    -- no progress events: 0 completed modules (documented choice, not NULL)
    coalesce(event_aggregates.completed_module_count, 0) as completed_module_count,
    case
        when coalesce(module_totals.total_module_count, 0) = 0 then null   -- no curriculum known
        -- DECIMAL(9,6), not DOUBLE: averages over exact values do not depend on thread scheduling
        else cast(
            coalesce(event_aggregates.completed_module_count, 0) * 1.0 / module_totals.total_module_count
            as decimal(9, 6)
        )
    end as completed_module_share,
    coalesce(event_aggregates.event_count, 0) as progress_event_count,
    event_aggregates.first_event_at_utc,
    event_aggregates.last_event_at_utc,
    {{ reporting_date() }} as reporting_date
from enrollments
left join module_totals
    on module_totals.training_id = enrollments.training_id
left join event_aggregates
    on event_aggregates.enrollment_id = enrollments.enrollment_id
