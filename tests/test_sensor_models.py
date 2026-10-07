"""The SensorReading contract: the gate every Kafka message passes through."""

import json
import time
from datetime import UTC

import pytest
from pydantic import ValidationError

from ingest.models import SensorReading

GOOD = {
    "event_id": "S-05-1790835312000000000",
    "sensor_id": "S-05",
    "ts": 1790835312.0,
    "soil_moisture_pct": 19.4,
}


def test_unix_seconds_become_an_aware_utc_datetime():
    reading = SensorReading.model_validate_json(json.dumps(GOOD))
    assert reading.ts.tzinfo is not None
    assert reading.ts.astimezone(UTC).isoformat() == "2026-10-01T06:15:12+00:00"


@pytest.mark.parametrize(
    "change, problem",
    [
        ({"soil_moisture_pct": 140}, "less than or equal to 100"),
        ({"soil_moisture_pct": -1}, "greater than or equal to 0"),
        ({"sensor_id": "sensor five"}, "pattern"),
        ({"ts": time.time() + 3600}, "ahead"),
    ],
)
def test_bad_values_are_rejected(change, problem):
    with pytest.raises(ValidationError, match=problem):
        SensorReading.model_validate(GOOD | change)


def test_missing_fields_are_all_reported():
    with pytest.raises(ValidationError) as info:
        SensorReading.model_validate_json('{"sensor_id": "S-99", "soil_moisture_pct": 140}')
    missing = {e["loc"][0] for e in info.value.errors()}
    assert {"event_id", "ts", "soil_moisture_pct"} <= missing


def test_unknown_fields_are_ignored_for_forward_compatibility():
    reading = SensorReading.model_validate(GOOD | {"battery_pct": 87})
    assert not hasattr(reading, "battery_pct")


def test_not_json_at_all_is_a_validation_error():
    with pytest.raises(ValidationError):
        SensorReading.model_validate_json(b"\x00 not json")
