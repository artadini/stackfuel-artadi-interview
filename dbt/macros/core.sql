{#- Use the custom schema name as is (staging, intermediate, marts, reporting) instead of
    dbt's default <target_schema>_<custom_schema> concatenation. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}


{#- KPI cutoff as a typed DATE literal. -#}
{% macro reporting_date() -%}
    cast('{{ var("reporting_date") }}' as date)
{%- endmacro %}


{#- Calendar date of a TIMESTAMPTZ in UTC, independent of the session time zone. -#}
{% macro utc_date(column) -%}
    cast(timezone('UTC', {{ column }}) as date)
{%- endmacro %}


{#- Deterministic de-duplication used by every staging model.

    relation     name of a CTE/relation that contains the key columns, the ordering columns and the
                 lineage columns payload_hash, run_id and source_row_number
    key_columns  list of columns that identify one source record
    order_by     ORDER BY of the ROW_NUMBER() window; the row with number 1 is kept

    Rows whose key contains a NULL are never collapsed into one record: each of them gets its own
    synthetic key, so they stay visible for quality analysis. No shared NULL sentinel is used. -#}
{% macro dedupe_latest(relation, key_columns, order_by) -%}
select * exclude (_row_number, _dedup_key, _source_ordinal)
from (
    select
        *,
        row_number() over (
            partition by _dedup_key
            order by {{ order_by }}
        ) as _row_number
    from (
        select
            *,
            case
                when {% for key in key_columns %}{{ key }} is null{% if not loop.last %} or {% endif %}{% endfor %}
                    then 'null-key|' || cast(_source_ordinal as varchar)
                else concat_ws('|', {{ key_columns | join(', ') }})
            end as _dedup_key
        from (
            select
                *,
                row_number() over (
                    order by coalesce(payload_hash, ''), coalesce(run_id, ''), coalesce(source_row_number, 0)
                ) as _source_ordinal
            from {{ relation }}
        )
    )
)
where _row_number = 1
{%- endmacro %}
