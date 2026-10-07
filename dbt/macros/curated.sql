{#- The curated layer has one table per snapshot date and dataset, named
    `curated."<YYYYMMDD>_<source>_<dataset>"` (for example `curated."20260901_crm_participants"`).
    The date part changes with every pipeline run, so staging cannot name the tables statically.

    curated_source('participants') returns a subquery that unions every curated table of the
    dataset, oldest snapshot first. The table names come from `meta.curation_log`, which the
    pipeline maintains (it is the authoritative list: dropped or replaced tables disappear from it).
    All curated tables of a dataset share one schema, `union all by name` only guards against
    column order. Rows are not de-duplicated here; staging does that (macro `dedupe_latest`). -#}
{% macro curated_source(dataset) -%}
    {%- if not execute -%}
        (select 1 as _parse_placeholder)
    {%- else -%}
        {%- set listing = run_query(
            "select curated_table from " ~ source('meta', 'curation_log')
            ~ " where dataset = '" ~ dataset ~ "' order by snapshot_date"
        ) -%}
        {%- set tables = listing.columns[0].values() -%}
        {%- if tables | length == 0 -%}
            {{ exceptions.raise_compiler_error(
                "No curated table found for dataset '" ~ dataset ~ "' in meta.curation_log. "
                ~ "Run `python pipeline.py` (or `make demo`) before dbt."
            ) }}
        {%- endif -%}
        (
        {%- for table in tables %}
            select * from "{{ target.database }}"."curated"."{{ table }}"
            {%- if not loop.last %} union all by name{% endif %}
        {%- endfor %}
        )
    {%- endif -%}
{%- endmacro %}
