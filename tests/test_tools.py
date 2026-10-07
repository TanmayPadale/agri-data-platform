"""The AI tools: validation happens before any SQL, and errors go back as text."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from ai.tools import (
    TOOL_SPECS,
    UnknownField,
    call_tool,
    get_field_conditions,
    list_fields_to_irrigate,
)

FIELD = {"field_id": "F-03", "field_name": "Field 3", "crop": "chilli", "location_name": "Jawali"}
DAY = {
    "day": date(2026, 10, 1),
    "rain_mm": 0.4,
    "temp_max_c": 31.5,
    "moisture_pct": 19.8,
    "moisture_3d_avg_pct": 23.2,
    "irrigate": True,
    "reason": "3-day moisture 23.2% is below 25% and rain 0.4 mm is below 2 mm",
    "moisture_threshold_pct": 25,
}


class FakeQuery:
    """Stands in for run_query: returns canned rows and records every call."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0) if self.results else []


def test_returns_labelled_days_for_a_field():
    query = FakeQuery([FIELD], [DAY])
    result = get_field_conditions("F-03", 7, query=query)
    assert result.crop == "chilli"
    assert result.moisture_threshold_pct == 25
    assert result.days[0].irrigate is True
    assert query.calls[1][1] == ("F-03", 7)  # values travel as parameters, never inside the SQL


@pytest.mark.parametrize(
    "field_id, days",
    [
        ("F-01'; DROP TABLE marts.dim_field;--", 7),  # injection attempt
        ("3", 7),
        ("F-003", 7),
        ("F-03", 0),
        ("F-03", 500),
    ],
)
def test_bad_input_is_rejected_before_any_sql(field_id, days):
    query = FakeQuery()
    with pytest.raises(ValidationError):
        get_field_conditions(field_id, days, query=query)
    assert query.calls == []


def test_unknown_field_is_a_clear_error():
    with pytest.raises(UnknownField, match="F-01 to F-10"):
        get_field_conditions("F-42", 7, query=FakeQuery([]))


def test_call_tool_turns_errors_into_text_for_the_model():
    content, is_error = call_tool("get_field_conditions", {"field_id": "3"}, query=FakeQuery())
    assert is_error
    assert "field_id" in json.loads(content)["error"]

    content, is_error = call_tool("drop_tables", {}, query=FakeQuery())
    assert is_error and "unknown tool" in content

    content, is_error = call_tool("get_field_conditions", {"field": "F-03"}, query=FakeQuery())
    assert is_error  # wrong argument name: a TypeError, reported, not raised


def test_call_tool_success_is_json():
    content, is_error = call_tool(
        "get_field_conditions", {"field_id": "F-03", "days": "7"}, query=FakeQuery([FIELD], [DAY])
    )
    assert not is_error
    assert json.loads(content)["days"][0]["moisture_3d_avg_pct"] == 23.2


def test_list_fields_to_irrigate():
    row = {k: DAY[k] for k in ("day", "moisture_3d_avg_pct", "rain_mm", "reason")}
    row |= {"field_id": "F-03", "crop": "chilli", "location_name": "Jawali"}
    result = list_fields_to_irrigate(query=FakeQuery([row]))
    assert result.day == date(2026, 10, 1)
    assert [f.field_id for f in result.fields_to_irrigate] == ["F-03"]


def test_list_fields_to_irrigate_when_none_qualify():
    result = list_fields_to_irrigate(query=FakeQuery([], [{"day": date(2026, 10, 1)}]))
    assert result.fields_to_irrigate == [] and result.day == date(2026, 10, 1)


def test_tool_schema_carries_the_rules_to_the_model():
    schema = TOOL_SPECS[0]["input_schema"]["properties"]
    assert schema["field_id"]["pattern"] == r"^F-\d{2}$"
    assert (schema["days"]["minimum"], schema["days"]["maximum"]) == (1, 30)
