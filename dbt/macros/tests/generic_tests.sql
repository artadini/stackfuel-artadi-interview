{#- Composite uniqueness: no combination of the listed columns occurs twice. -#}
{% test unique_combination_of_columns(model, combination_of_columns) %}
select {{ combination_of_columns | join(', ') }}, count(*) as row_count
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1
{% endtest %}


{#- Numeric range: values (where not NULL) must lie in [min_value, max_value]. -#}
{% test value_between(model, column_name, min_value=none, max_value=none) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and (
      false
      {% if min_value is not none %} or {{ column_name }} < {{ min_value }}{% endif %}
      {% if max_value is not none %} or {{ column_name }} > {{ max_value }}{% endif %}
  )
{% endtest %}


{#- Timestamp range: present values must be inside [min_value, max_value] (dates as strings). -#}
{% test timestamp_in_range(model, column_name, min_value, max_value) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and (
      {{ column_name }} < cast('{{ min_value }}' as timestamp with time zone)
      or {{ column_name }} >= cast('{{ max_value }}' as timestamp with time zone)
  )
{% endtest %}


{#- Row-level comparison: column_name must be <= other_column (both not NULL). -#}
{% test column_lte_column(model, column_name, other_column) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and {{ other_column }} is not null
  and {{ column_name }} > {{ other_column }}
{% endtest %}


{#- Every row of the model carries the given constant (e.g. the reporting date). -#}
{% test equals_constant(model, column_name, value) %}
select *
from {{ model }}
where {{ column_name }} is distinct from {{ value }}
{% endtest %}


{#- A list column may only contain the given values. -#}
{% test list_values_in(model, column_name, values) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and len(list_filter({{ column_name }}, x -> x not in ({% for v in values %}'{{ v }}'{% if not loop.last %}, {% endif %}{% endfor %}))) > 0
{% endtest %}


{#- Staging and the curated tables of its dataset must hold exactly the same keys.
    * `missing_in_staging`: a key exists in a curated table but not here. An incremental run skipped data
      (for example a load for an older snapshot date, or a rebuilt curated table).
    * `not_in_curated`: a key exists here but in no curated table any more. The source delivered less than
      before (for example a rerun against a wrong or stale source), and an incremental run cannot remove rows,
      so staging and the layers built on the curated tables (the coach matching) disagree.
    Fix: `make dbt-full-refresh`, or for a skipped older day
    `--vars '{incremental_from_snapshot_date: <that date>}'`. -#}
{% test staging_matches_curated_keys(model, dataset, key_columns) %}
with curated_keys as (
    select distinct {{ key_columns | join(', ') }}
    from {{ curated_source(dataset) }}
    where {% for key in key_columns %}{{ key }} is not null{% if not loop.last %} and {% endif %}{% endfor %}
),

staging_keys as (
    select distinct {{ key_columns | join(', ') }}
    from {{ model }}
    where {% for key in key_columns %}{{ key }} is not null{% if not loop.last %} and {% endif %}{% endfor %}
)

select 'missing_in_staging' as problem, * from (select * from curated_keys except select * from staging_keys)
union all
select 'not_in_curated' as problem, * from (select * from staging_keys except select * from curated_keys)
{% endtest %}
