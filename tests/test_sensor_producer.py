"""The simulator and the wire format, without a broker."""

import json
import random
from datetime import UTC, datetime, timedelta

from ingest.models import SensorReading
from ingest.sensor_producer import (
    REPLAY_HEADER,
    ReadingProducer,
    build_fleet,
    history_timestamps,
    load_sensors,
    make_reading,
    serialize,
)


def test_twenty_sensors_two_per_field():
    sensors = load_sensors()
    assert len(sensors) == 20
    per_field: dict[str, int] = {}
    for s in sensors:
        per_field[s.field_id] = per_field.get(s.field_id, 0) + 1
    assert set(per_field.values()) == {2}


def test_readings_stay_in_a_plausible_range():
    rng = random.Random(7)
    fleet = build_fleet(load_sensors(), rng)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    values = [
        make_reading(sim, start + timedelta(minutes=10 * i), rng).soil_moisture_pct
        for i in range(2000)
        for sim in fleet[:3]
    ]
    assert 3 <= min(values) and max(values) <= 60


def test_history_is_on_a_fixed_grid():
    now = datetime(2026, 10, 1, 6, 17, 30, tzinfo=UTC)
    times = list(history_timestamps(1, timedelta(minutes=10), now))
    assert times[0].minute % 10 == 0 and times[0].second == 0
    assert all(b - a == timedelta(minutes=10) for a, b in zip(times, times[1:], strict=False))
    assert times[-1] < now


def test_same_history_gives_same_event_ids():
    """Replaying history must produce the same keys, or replays would create new rows."""
    at = datetime(2026, 10, 1, 6, 10, tzinfo=UTC)
    a = make_reading(build_fleet(load_sensors(), random.Random(1))[0], at, random.Random(1))
    b = make_reading(build_fleet(load_sensors(), random.Random(2))[0], at, random.Random(2))
    assert a.event_id == b.event_id


def test_wire_format_round_trips_through_the_contract():
    rng = random.Random(3)
    sim = build_fleet(load_sensors(), rng)[4]
    reading = make_reading(sim, datetime(2026, 10, 1, 6, 15, 12, tzinfo=UTC), rng)
    payload = json.loads(serialize(reading))
    assert isinstance(payload["ts"], float)  # Unix seconds on the wire
    assert SensorReading.model_validate(payload) == reading


def test_headers_reach_the_kafka_client():
    """History is sent with the replay header; live readings without it."""

    class RecordingProducer:
        def __init__(self):
            self.calls = []

        def produce(self, topic, **kwargs):
            self.calls.append(kwargs)

        def poll(self, timeout):
            return 0

    client = RecordingProducer()
    out = ReadingProducer(client, "sensor.readings")
    out.send("S-01", b"{}")
    out.send("S-01", b"{}", headers=REPLAY_HEADER)
    assert [c["headers"] for c in client.calls] == [None, [("replay", b"1")]]
