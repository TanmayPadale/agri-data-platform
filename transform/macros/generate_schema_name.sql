{#
  By default dbt glues a custom schema onto the target schema, so `+schema: marts`
  would become "public_marts". This project wants the exact names raw, staging,
  marts and snapshots, because the SQL files, the read-only MCP role (Day 8) and
  the docs all refer to them. Overriding this one macro changes that everywhere.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
