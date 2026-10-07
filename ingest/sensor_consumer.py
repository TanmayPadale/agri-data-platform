"""Day 2: Kafka topic sensor.readings -> raw.sensor_readings, crash-safe.

For every message, in this order:

  1. validate with SensorReading         bad -> dead-letter topic (original bytes + reason),
                                                commit, move on: one bad message costs one message
  2. Redis EXISTS seen:{event_id}        yes -> stored recently: commit, skip the database
  3. INSERT ... ON CONFLICT DO NOTHING   the primary key is the real duplicate guard
  4. Redis SET seen + HSET latest        only after the write, so Redis never claims more
                                         than Postgres holds
  5. commit the Kafka offset             last: a crash anywhere above means the message is
                                         delivered again, never skipped

That is at-least-once delivery into an idempotent sink. A crash causes
reprocessing, and the primary key turns reprocessing into a no-op, so the table
ends up exactly-once without Kafka transactions.

Run it (Kafka, Redis and Postgres up: make up-stream):

    uv run python -m ingest.sensor_consumer                      # until Ctrl+C
    uv run python -m ingest.sensor_consumer --exit-when-idle 15  # stop after 15 quiet seconds
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
import redis
from confluent_kafka import Consumer, Message, Producer
from pydantic import ValidationError

from ingest.config import (
    consumer_group,
    dlq_topic,
    kafka_bootstrap,
    redis_host,
    redis_port,
    sensor_topic,
)
from ingest.db import connect
from ingest.models import SensorReading

log = logging.getLogger(__name__)

INSERT_SQL = """
INSERT INTO raw.sensor_readings
    (sensor_id, ts, soil_moisture_pct, event_id, kafka_partition, kafka_offset)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (sensor_id, ts) DO NOTHING
"""
SEEN_TTL_SECONDS = 3600  # Redis remembers an event id for one hour


@dataclass
class Counts:
    stored: int = 0  # new row written
    duplicate: int = 0  # reached Postgres, but the row was already there
    skipped: int = 0  # Redis said "seen", so Postgres was never asked
    dlq: int = 0  # failed validation, parked in the dead-letter topic


class SensorLoader:
    """The per-message logic, with Kafka, Postgres and Redis passed in.

    Passing the clients in (instead of creating them here) is what lets the tests
    run this exact code against fakes, and the Day 7 metrics wrap it unchanged.
    """

    def __init__(
        self,
        consumer: Any,
        dlq_producer: Any,
        conn: psycopg.Connection,
        redis_client: Any = None,
        *,
        dlq_topic_name: str | None = None,
        crash_after_write: int | None = None,
    ) -> None:
        self.consumer = consumer
        self.dlq = dlq_producer
        self.conn = conn  # autocommit: each INSERT is durable before the offset commit
        self.redis = redis_client
        self.dlq_topic = dlq_topic_name or dlq_topic()
        self.crash_after_write = crash_after_write
        self.counts = Counts()
        self.writes = 0
        self._redis_warned = False

    # ------------------------------------------------------------ one message

    def handle(self, msg: Message) -> str:
        try:
            value = msg.value()
            if value is None:
                raise ValueError("empty message value")
            reading = SensorReading.model_validate_json(value)
        except (ValidationError, ValueError) as exc:
            self.send_to_dlq(msg, exc)
            self.commit(msg)
            self.counts.dlq += 1
            return "dlq"

        if self.already_seen(reading.event_id):
            self.commit(msg)
            self.counts.skipped += 1
            return "skipped"

        inserted = self.insert(reading, msg)
        self.writes += 1
        if self.crash_after_write and self.writes >= self.crash_after_write:
            # Lab hook (AGRI_CRASH_AFTER_WRITE): die like `kill -9` at the most dangerous
            # moment. The row is in Postgres, but neither Redis nor Kafka knows yet.
            log.error("AGRI_CRASH_AFTER_WRITE=%s reached: exiting before the commit", self.writes)
            os._exit(137)
        self.mark_seen(reading)
        self.commit(msg)
        if inserted:
            self.counts.stored += 1
            return "stored"
        self.counts.duplicate += 1
        return "duplicate"

    def insert(self, reading: SensorReading, msg: Message) -> bool:
        cur = self.conn.execute(
            INSERT_SQL,
            (
                reading.sensor_id,
                reading.ts,
                reading.soil_moisture_pct,
                reading.event_id,
                msg.partition(),
                msg.offset(),
            ),
        )
        return cur.rowcount == 1  # 0 means ON CONFLICT found the row already there

    def commit(self, msg: Message) -> None:
        # Synchronous, and for this message only: the group's bookmark for this
        # partition moves to offset + 1, "the next record to read".
        self.consumer.commit(message=msg, asynchronous=False)

    def send_to_dlq(self, msg: Message, error: Exception) -> None:
        headers = [
            ("error", describe(error)[:1000].encode()),
            ("source_topic", str(msg.topic()).encode()),
            ("source_partition", str(msg.partition()).encode()),
            ("source_offset", str(msg.offset()).encode()),
        ]
        # The original bytes, untouched, so the message can be fixed and replayed later.
        self.dlq.produce(self.dlq_topic, key=msg.key(), value=msg.value(), headers=headers)
        # Wait for the broker to confirm before the caller commits. Committing first
        # and crashing would lose the bad message entirely.
        if self.dlq.flush(5) > 0:
            raise RuntimeError("DLQ write not confirmed; not committing, so it will be retried")
        log.warning(
            "sent to DLQ: partition %s offset %s: %s",
            msg.partition(),
            msg.offset(),
            describe(error),
        )

    # ------------------------------------------------------------ Redis fast path

    def already_seen(self, event_id: str) -> bool:
        return bool(self._redis_call(lambda r: r.exists(f"seen:{event_id}")))

    def mark_seen(self, reading: SensorReading) -> None:
        def _mark(r: Any) -> None:
            pipe = r.pipeline()  # both commands in one round trip
            pipe.set(f"seen:{reading.event_id}", 1, ex=SEEN_TTL_SECONDS)
            pipe.hset(
                f"latest:{reading.sensor_id}",
                mapping={"ts": reading.ts.isoformat(), "moisture": reading.soil_moisture_pct},
            )
            pipe.execute()

        self._redis_call(_mark)

    def _redis_call(self, fn: Any) -> Any:
        """Redis only saves work. If it is down, carry on without it: the primary
        key still guarantees correctness, writes are just a little slower."""
        if self.redis is None:
            return None
        try:
            return fn(self.redis)
        except redis.RedisError as exc:
            if not self._redis_warned:
                log.warning("Redis unavailable (%s); continuing without the fast path", exc)
                self._redis_warned = True
            return None

    # ------------------------------------------------------------ the loop

    def run(self, stop: threading.Event, exit_when_idle: float | None = None) -> Counts:
        last_message = time.monotonic()
        while not stop.is_set():
            msg = self.consumer.poll(1.0)
            if msg is None:
                if exit_when_idle and time.monotonic() - last_message > exit_when_idle:
                    log.info("idle for %ss, stopping", exit_when_idle)
                    break
                continue
            if msg.error():
                log.warning("consumer error: %s", msg.error())
                continue
            last_message = time.monotonic()
            self.handle(msg)
            total = self.counts.stored + self.counts.duplicate + self.counts.skipped
            if total and total % 5000 == 0:
                log.info("progress: %s", asdict(self.counts))
        return self.counts


def describe(error: Exception) -> str:
    """One line per problem, e.g. "ts: Field required; soil_moisture_pct: Input should be ..."."""
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'message'}: {e['msg']}" for e in error.errors()
        )
    return str(error)


def _log_assign(consumer: Consumer, partitions: list[Any]) -> None:
    log.info("rebalance: now also reading partitions %s", sorted(p.partition for p in partitions))


def _log_revoke(consumer: Consumer, partitions: list[Any]) -> None:
    log.info("rebalance: handing over partitions %s", sorted(p.partition for p in partitions))


def new_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": kafka_bootstrap(),
            "group.id": consumer_group(),
            # Kafka 4's consumer group protocol (KIP-848): the broker computes the
            # assignment and rebalances are incremental, so a joining or leaving
            # member only pauses the partitions that actually move.
            "group.protocol": "consumer",
            "enable.auto.commit": False,  # we decide when a message counts as done
            "auto.offset.reset": "earliest",  # a brand-new group starts at the oldest record
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kafka sensor.readings -> Postgres.")
    parser.add_argument(
        "--exit-when-idle", type=float, help="stop after N seconds with no messages"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    consumer = new_consumer()
    consumer.subscribe([sensor_topic()], on_assign=_log_assign, on_revoke=_log_revoke)
    dlq = Producer({"bootstrap.servers": kafka_bootstrap(), "enable.idempotence": True})
    cache = redis.Redis(redis_host(), redis_port(), socket_timeout=1, socket_connect_timeout=1)
    crash_after = int(os.environ.get("AGRI_CRASH_AFTER_WRITE", "0")) or None

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):  # Kubernetes sends SIGTERM before killing a pod
        signal.signal(sig, lambda *_: stop.set())

    with connect(autocommit=True) as conn:
        loader = SensorLoader(consumer, dlq, conn, cache, crash_after_write=crash_after)
        try:
            counts = loader.run(stop, exit_when_idle=args.exit_when_idle)
        finally:
            consumer.close()  # leave the group now, so the rebalance starts immediately
    print(f"done: {asdict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
