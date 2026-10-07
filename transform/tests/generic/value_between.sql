{#
  A custom generic test: fails with every row whose value is outside
  [min_value, max_value]. Use it in YAML like the built-in tests:

      data_tests:
        - value_between:
            arguments: {min_value: 0, max_value: 100}

  A test is just a query that returns the bad rows; zero rows means it passed.
  Nulls are ignored here on purpose: not_null is a separate, clearer test.
#}
{% test value_between(model, column_name, min_value, max_value) %}

select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})

{% endtest %}
