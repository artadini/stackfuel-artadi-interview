{% docs col_run_id %}ID of the pipeline run that extracted the selected source version into the raw layer (`raw_run_id`, lineage).{% enddocs %}

{% docs col_ingested_at %}UTC instant at which that extraction run started (`raw_ingested_at`).{% enddocs %}

{% docs col_snapshot_date %}Snapshot date of the raw/curated table the selected version comes from (pipeline day, not a business date).{% enddocs %}

{% docs col_source_as_of_date %}Business date of the source extract where the source provides one (catalog `as_of`, workbook "Stand"); NULL otherwise. Source metadata, not a row modification time.{% enddocs %}

{% docs col_quality_flags %}Row-level findings of the raw curated layer as a list of rule names (for example `missing_ampel`, `duplicate_source_id`). Warnings only; errors are in `validation_status`.{% enddocs %}

{% docs col_validation_status %}Curated validation result: `valid`, `review` (usable but needs attention, e.g. ambiguous match) or `quarantined` (record-level error: the typed value is NULL, the original text is kept in the `*_raw` column of the curated table and in the raw layer).{% enddocs %}

{% docs col_is_quarantined %}True when the raw curated row has a record-level error. Marts exclude these rows; staging keeps them visible.{% enddocs %}

{% docs col_is_deleted %}Always false for now: the pipeline does not detect hard deletes at the source. The column stays so marts keep their `is_deleted` filter and deletion detection can be added later.{% enddocs %}

{% docs col_reporting_date %}KPI cutoff ("Stichtag") the report is evaluated at: dbt var `reporting_date` = 2026-08-31.{% enddocs %}

{% docs col_data_quality_flags %}List of data-quality caveats that apply to the row, derived only from facts in the data (counts of affected enrollments are in the neighbouring columns).{% enddocs %}
