"""The WeatherDay contract: what it accepts and, more importantly, what it rejects."""

from datetime import date

import pytest
from pydantic import ValidationError

from ingest.models import WeatherDay

GOOD = {"location_id": 1, "day": "2026-09-28", "t_max_c": 31.2, "t_min_c": 20.1, "rain_mm": 4.0}


def test_valid_row_is_parsed_and_typed():
    row = WeatherDay(**GOOD)
    assert row.day == date(2026, 9, 28)  # the string became a real date
    assert row.et0_mm is None  # optional field defaults


def test_negative_rain_is_rejected():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        WeatherDay(**GOOD | {"rain_mm": -1})


def test_missing_rain_is_rejected_not_defaulted_to_zero():
    # A null from the API must not quietly become a dry day.
    with pytest.raises(ValidationError):
        WeatherDay(**GOOD | {"rain_mm": None})


def test_impossible_temperature_is_rejected():
    with pytest.raises(ValidationError):
        WeatherDay(**GOOD | {"t_max_c": 75})


def test_min_above_max_is_rejected():
    with pytest.raises(ValidationError, match="above t_max_c"):
        WeatherDay(**GOOD | {"t_min_c": 35.0})


def test_rows_are_immutable():
    row = WeatherDay(**GOOD)
    with pytest.raises(ValidationError):
        row.rain_mm = 0  # type: ignore[misc]
