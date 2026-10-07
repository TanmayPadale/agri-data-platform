"""Day 6: the only ways an AI model can touch the farm data.

The model never writes SQL. It can ask for one of these small, task-shaped,
read-only operations by name, and this code decides whether and how to run it:

  1. the arguments are validated by Pydantic BEFORE any SQL runs
     ("F-01'; DROP TABLE x;--" fails the ^F-\\d{2}$ pattern and never reaches Postgres)
  2. the SQL is fixed and parameterised; the model only supplies values
  3. results are small and labelled, so the model can quote them accurately

Day 8 serves exactly these functions over MCP, connected as a read-only role.
All SQL goes through a `query` function that tests replace with a fake.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from pydantic import BaseModel, Field, ValidationError

from ingest.db import connect

Query = Callable[[str, tuple[Any, ...]], list[dict[str, Any]]]

FIELD_ID_PATTERN = r"^F-\d{2}$"  # also the MCP tool's schema (Day 8), so both check the same


def run_query(
    sql: str, params: tuple[Any, ...] = (), dsn: str | None = None
) -> list[dict[str, Any]]:
    with connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ---------------------------------------------------------------- contracts


class FieldConditionsInput(BaseModel):
    field_id: str = Field(pattern=FIELD_ID_PATTERN, description="Field id, for example F-03")
    days: int = Field(default=7, ge=1, le=30, description="How many recent days, 1 to 30")


class DayConditions(BaseModel):
    day: date
    rain_mm: float | None
    temp_max_c: float | None
    moisture_pct: float | None = Field(description="That day's average soil moisture")
    moisture_3d_avg_pct: float | None = Field(description="Average over the last 3 days")
    irrigate: bool | None = Field(description="true water it, false no need, null unknown")
    reason: str


class FieldConditions(BaseModel):
    field_id: str
    field_name: str
    crop: str
    location_name: str
    moisture_threshold_pct: float | None
    days: list[DayConditions]


class FieldDecision(BaseModel):
    field_id: str
    crop: str
    location_name: str
    moisture_3d_avg_pct: float | None
    rain_mm: float | None
    reason: str


class IrrigationList(BaseModel):
    day: date | None = Field(description="The latest day with decisions")
    fields_to_irrigate: list[FieldDecision]


class UnknownField(LookupError):
    pass


# ---------------------------------------------------------------- the tools


def get_field_conditions(
    field_id: str, days: int = 7, *, query: Query = run_query
) -> FieldConditions:
    """Rain, temperature, soil moisture and the irrigation decision for one field."""
    args = FieldConditionsInput(field_id=field_id, days=days)  # raises before any SQL
    fields = query(
        "SELECT field_id, field_name, crop, location_name FROM marts.dim_field WHERE field_id = %s",
        (args.field_id,),
    )
    if not fields:
        raise UnknownField(f"no field {args.field_id}; fields are F-01 to F-10")
    rows = query(
        """
        SELECT day, rain_mm, temp_max_c, moisture_pct, moisture_3d_avg_pct, irrigate, reason,
               moisture_threshold_pct
        FROM marts.agg_irrigation_signal
        WHERE field_id = %s
          AND day > (SELECT max(day) FROM marts.agg_irrigation_signal) - %s
        ORDER BY day
        """,
        (args.field_id, args.days),
    )
    threshold = rows[0]["moisture_threshold_pct"] if rows else None
    return FieldConditions(
        **fields[0],
        moisture_threshold_pct=threshold,
        days=[
            DayConditions(**{k: v for k, v in r.items() if k != "moisture_threshold_pct"})
            for r in rows
        ],
    )


def list_fields_to_irrigate(*, query: Query = run_query) -> IrrigationList:
    """Every field flagged for irrigation on the latest decided day."""
    rows = query(
        """
        SELECT s.day, s.field_id, s.crop, f.location_name,
               s.moisture_3d_avg_pct, s.rain_mm, s.reason
        FROM marts.agg_irrigation_signal AS s
        JOIN marts.dim_field AS f ON f.field_id = s.field_id
        WHERE s.irrigate
          AND s.day = (SELECT max(day) FROM marts.agg_irrigation_signal WHERE irrigate IS NOT NULL)
        ORDER BY s.field_id
        """,
        (),
    )
    if not rows:
        latest = query(
            "SELECT max(day) AS day FROM marts.agg_irrigation_signal WHERE irrigate IS NOT NULL", ()
        )
        return IrrigationList(day=latest[0]["day"] if latest else None, fields_to_irrigate=[])
    return IrrigationList(
        day=rows[0]["day"],
        fields_to_irrigate=[
            FieldDecision(**{k: v for k, v in r.items() if k != "day"}) for r in rows
        ],
    )


# ---------------------------------------------------------------- what the model sees

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_field_conditions",
        "description": (
            "Recent daily rain (mm), maximum temperature, soil moisture (%) and the "
            "irrigation decision with its reason for ONE field, from the farm database. "
            "Use it for any question about a specific field's current or recent "
            "conditions or whether it needs water. Field ids look like F-03."
        ),
        "input_schema": FieldConditionsInput.model_json_schema(),
    }
]


def call_tool(
    name: str, arguments: dict[str, Any], *, query: Query = run_query
) -> tuple[str, bool]:
    """Run a tool the model asked for. Returns (JSON text, is_error).

    Errors go back to the model as text instead of crashing the loop, so it can
    correct itself (for example after asking for field "3" instead of "F-03").
    """
    try:
        if name != "get_field_conditions":
            raise LookupError(f"unknown tool {name!r}")
        result = get_field_conditions(**arguments, query=query)
        return result.model_dump_json(), False
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return json.dumps({"error": f"invalid arguments: {problems}"}), True
    except (LookupError, TypeError) as exc:
        return json.dumps({"error": str(exc)}), True
