{#- Incremental window of every staging model (all of them are incremental).

    The Python pipeline writes one curated table per dataset and snapshot date
    (`curated."<YYYYMMDD>_<source>_<dataset>"`, see macro `curated_source`), each with the column
    `snapshot_date`. The first table of a dataset is a complete load; later tables only hold the
    records that are new or changed since the previous load (high watermark), so reading just the
    newest day(s) is enough to keep staging current.

    Full build (first run, `--full-refresh`):  every snapshot day, i.e. the complete history.
    Incremental run (default):                 snapshot_date >= the newest snapshot_date already in
                                               the staging table. `>=` (not `>`) on purpose: a rerun
                                               on the same day replaces that day's table, and dbt days
                                               may be skipped (several new days are then caught up in
                                               one run).
    Explicit day (backfill or "today only"):   dbt build --vars '{incremental_from_snapshot_date: 2026-10-04}'

    Late-arriving data needs no extra handling: it reaches the raw layer on the day the pipeline
    first sees it and is therefore selected by its snapshot_date, whatever its event time is. -#}
{% macro incremental_snapshot_filter() -%}
    {%- if not is_incremental() -%}
        true
    {%- elif var('incremental_from_snapshot_date', none) is not none -%}
        snapshot_date >= cast('{{ var("incremental_from_snapshot_date") }}' as date)
    {%- else -%}
        snapshot_date >= (select coalesce(max(snapshot_date), cast('1900-01-01' as date)) from {{ this }})
    {%- endif -%}
{%- endmacro %}
