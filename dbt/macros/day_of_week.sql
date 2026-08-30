{#
    DuckDB's dayofweek() has no Postgres equivalent by that name; Postgres uses
    extract(dow from ...). Both return 0=Sunday..6=Saturday, so the two branches agree.
    Tests compile both targets offline. UTC extraction matches the UTC silver timestamps
    even when the Postgres session uses a different timezone.
#}
{% macro day_of_week(column) %}
  {% if target.type == 'postgres' %}
    cast(extract(dow from {{ column }} at time zone 'UTC') as integer)
  {% else %}
    dayofweek({{ column }})
  {% endif %}
{% endmacro %}
