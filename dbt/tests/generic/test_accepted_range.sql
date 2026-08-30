{#
    Self-contained replacement for dbt_utils.accepted_range: fails for any row where
    column_name falls outside [min_value, max_value]. Implemented locally (instead of
    depending on the dbt_utils package) so the dbt test suite has zero external package
    dependencies and stays fully offline-verifiable, matching this project's constraint of
    never requiring a live network call to run tests.
#}
{% test accepted_range(model, column_name, min_value=none, max_value=none) %}

select *
from {{ model }}
where
    {{ column_name }} is not null
    and (
        {% if min_value is not none %} {{ column_name }} < {{ min_value }} {% else %} false {% endif %}
        or
        {% if max_value is not none %} {{ column_name }} > {{ max_value }} {% else %} false {% endif %}
    )

{% endtest %}
