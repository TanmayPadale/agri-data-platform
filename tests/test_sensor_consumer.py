"""The consumer's per-message logic against fake Kafka and Redis and a real Postgres.

These are the Day 2 break-it labs in test form: duplicates, poison messages, a
crash between the write and the commit, and Redis going away.
"""

import json
import threading
from collections import deque

import pytest

from ingest.models import SensorReading
from ingest.sensor_consumer import SensorLoader
from tests.fakes.kafka import FakeConsumer, FakeMessage, FakeProducer, FakeRedis

pytestmark = pytest.mark.db

READING = {
    "event_id": "S-05-1790835312000000000",
    "sensor_id": "S-05",
    "ts": 1790835312.0,
    "soil_moisture_pct": 19.4,
}


def message(payload=READING, offset=4182) -> FakeMessage:
    value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return FakeMessage(value, _offset=offset)


@pytest.fixture
def autocommit_conn(test_dsn, db):
    import psycopg

    with psycopg.connect(test_dsn, autocommit=True) as conn:
        yield conn


def make_loader(conn, redis_client=None, **kwargs):
    return SensorLoader(FakeConsumer(), FakeProducer(), conn, redis_client, **kwargs)


def rows(db):
    return db.execute(
        "SELECT sensor_id, soil_moisture_pct::float, kafka_offset FROM raw.sensor_readings"
    ).fetchall()


def test_happy_path_stores_then_commits(autocommit_conn, db):
    cache = FakeRedis()
    loader = make_loader(autocommit_conn, cache)
    assert loader.handle(message()) == "stored"
    assert rows(db) == [("S-05", 19.4, 4182)]
    assert loader.consumer.committed == [(3, 4183)]  # the next offset to read
    assert "seen:S-05-1790835312000000000" in cache.store
    assert cache.store["latest:S-05"]["moisture"] == 19.4


def test_redelivery_after_a_crash_writes_nothing_new(autocommit_conn, db):
    """The crash lab: the row was written, then the process died before Redis and
    the offset commit. Kafka delivers the same record again to a fresh process."""
    first = make_loader(autocommit_conn, FakeRedis())
    first.insert(SensorReading.model_validate(READING), message())  # step 3 happened...
    assert first.consumer.committed == []  # ...then the process died: no Redis mark, no commit

    restarted = make_loader(autocommit_conn, FakeRedis())  # Redis never heard of it either
    assert restarted.handle(message()) == "duplicate"  # ON CONFLICT DO NOTHING absorbed it
    assert len(rows(db)) == 1
    assert restarted.consumer.committed == [(3, 4183)]


def test_redis_fast_path_skips_the_database(autocommit_conn, db):
    cache = FakeRedis()
    loader = make_loader(autocommit_conn, cache)
    loader.handle(message())
    assert loader.handle(message(offset=4190)) == "skipped"
    assert loader.counts.skipped == 1
    assert len(rows(db)) == 1


def test_poison_message_goes_to_the_dlq_and_the_partition_moves_on(autocommit_conn, db):
    loader = make_loader(autocommit_conn, FakeRedis())
    poison = message({"sensor_id": "S-99", "soil_moisture_pct": 140}, offset=77)
    assert loader.handle(poison) == "dlq"
    [parked] = loader.dlq.sent
    assert parked["topic"] == "sensor.readings.dlq"
    assert parked["value"] == poison.value()  # original bytes, untouched
    assert parked["headers"]["source_offset"] == b"77"
    assert b"soil_moisture_pct" in parked["headers"]["error"]
    assert loader.consumer.committed == [(3, 78)]
    assert rows(db) == []


def test_unconfirmed_dlq_write_means_no_commit(autocommit_conn, db):
    loader = make_loader(autocommit_conn, FakeRedis())
    loader.dlq.unconfirmed = 1
    with pytest.raises(RuntimeError, match="not committing"):
        loader.handle(message(b"garbage"))
    assert loader.consumer.committed == []  # Kafka will deliver it again


def test_redis_down_still_writes_correctly(autocommit_conn, db):
    loader = make_loader(autocommit_conn, FakeRedis(down=True))
    assert loader.handle(message()) == "stored"
    assert loader.handle(message(offset=4183)) == "duplicate"  # Postgres caught it instead
    assert len(rows(db)) == 1


def test_run_drains_the_queue_and_stops_when_idle(autocommit_conn, db):
    loader = make_loader(autocommit_conn, FakeRedis())
    batch = [READING | {"event_id": f"S-05-{i}", "ts": READING["ts"] + i * 2} for i in range(25)]
    loader.consumer.queue = deque(message(p, offset=i) for i, p in enumerate(batch))
    counts = loader.run(threading.Event(), exit_when_idle=0.01)
    assert counts.stored == 25
    assert len(rows(db)) == 25
