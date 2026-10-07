"""Day 2: twenty simulated soil-moisture sensors publishing to Kafka.

Each sensor random-walks around a level that drifts slowly with its field, and
sends one reading every 2 seconds:

    key   = sensor_id               decides the partition, so each sensor's readings stay in order
    value = SensorReading as JSON   validated before it is sent, same contract the consumer uses

Run it (Kafka must be up: make up-stream):

    uv run python -m ingest.sensor_producer                    # live, every 2 s, Ctrl+C stops
    uv run python -m ingest.sensor_producer --history-days 21  # 3 weeks of past readings
    uv run python -m ingest.sensor_producer --poison           # one malformed message (DLQ lab)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import random
import signal
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from confluent_kafka import KafkaError, Message, Producer

from ingest.config import REPO_ROOT, kafka_bootstrap, sensor_topic
from ingest.models import Sensor, SensorReading

log = logging.getLogger(__name__)

SENSORS_CSV = REPO_ROOT / "transform" / "seeds" / "sensors.csv"

# The level each field hovers around (% volumetric soil moisture). Paddy rice is
# wet, the chilli and grape fields run dry, and the rest sit near the 25% line,
# so the irrigation signal has real decisions to make.
FIELD_BASELINE = {
    "F-01": 27.0,  # marigold
    "F-02": 26.0,  # onion
    "F-03": 23.0,  # chilli
    "F-04": 33.0,  # sugarcane
    "F-05": 25.0,  # tomato
    "F-06": 44.0,  # rice
    "F-07": 24.0,  # cotton
    "F-08": 28.0,  # citrus
    "F-09": 22.0,  # wine grapes
    "F-10": 25.0,  # almonds
}
DRIFT_PERIOD = timedelta(days=5)  # fields dry out and recover over a few days
DRIFT_AMPLITUDE = 4.0


def load_sensors(path: Path = SENSORS_CSV) -> list[Sensor]:
    with path.open(newline="") as f:
        return [Sensor.model_validate(row) for row in csv.DictReader(f)]


@dataclass
class SimulatedSensor:
    sensor: Sensor
    level: float
    phase: float = field(default=0.0)

    def target(self, at: datetime) -> float:
        """The level this sensor is pulled towards at a given moment."""
        cycle = 2 * math.pi * (at.timestamp() / DRIFT_PERIOD.total_seconds()) + self.phase
        return FIELD_BASELINE[self.sensor.field_id] + DRIFT_AMPLITUDE * math.sin(cycle)

    def read(self, at: datetime, rng: random.Random) -> float:
        # Mean reversion plus noise: move 5% of the way towards the target, then wobble.
        self.level += 0.05 * (self.target(at) - self.level) + rng.uniform(-0.6, 0.6)
        self.level = min(60.0, max(3.0, self.level))
        return round(self.level, 2)


def build_fleet(sensors: list[Sensor], rng: random.Random) -> list[SimulatedSensor]:
    phases = {field_id: rng.uniform(0, 2 * math.pi) for field_id in FIELD_BASELINE}
    return [
        SimulatedSensor(s, FIELD_BASELINE[s.field_id] + rng.uniform(-2, 2), phases[s.field_id])
        for s in sensors
    ]


def make_reading(sim: SimulatedSensor, at: datetime, rng: random.Random) -> SensorReading:
    # The event id is derived from sensor and timestamp, so replaying the same
    # history produces the same ids, and the same primary keys downstream.
    nanos = int(at.timestamp() * 1_000_000_000)
    return SensorReading(
        event_id=f"{sim.sensor.sensor_id}-{nanos}",
        sensor_id=sim.sensor.sensor_id,
        ts=at,
        soil_moisture_pct=sim.read(at, rng),
    )


def history_timestamps(days: int, step: timedelta, now: datetime) -> Iterator[datetime]:
    """Times on a fixed grid (e.g. every 10 minutes) from `days` ago up to now."""
    step_s = int(step.total_seconds())
    start = int((now - timedelta(days=days)).timestamp()) // step_s * step_s
    for t in range(start, int(now.timestamp()), step_s):
        yield datetime.fromtimestamp(t, UTC)


def serialize(reading: SensorReading) -> bytes:
    payload = reading.model_dump(mode="json")
    payload["ts"] = reading.ts.timestamp()  # Unix seconds on the wire, like a real device
    return json.dumps(payload).encode()


class ReadingProducer:
    """Thin wrapper that counts delivery reports and handles a full local queue."""

    def __init__(self, producer: Producer, topic: str) -> None:
        self.producer = producer
        self.topic = topic
        self.delivered = 0
        self.failed = 0

    def _on_delivery(self, err: KafkaError | None, msg: Message) -> None:
        if err is not None:
            self.failed += 1
            log.error("delivery failed for key %s: %s", msg.key(), err)
        else:
            self.delivered += 1

    def send(self, key: str, value: bytes) -> None:
        while True:
            try:
                self.producer.produce(
                    self.topic, key=key, value=value, on_delivery=self._on_delivery
                )
                break
            except BufferError:
                # The client's local queue is full: let it send what it has, then try again.
                self.producer.poll(0.5)
        self.producer.poll(0)  # serve delivery callbacks without blocking

    def flush(self) -> int:
        return self.producer.flush(30)


def new_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": kafka_bootstrap(),
            # The broker drops duplicates caused by the producer's own retries
            # (it tracks a producer id and a sequence number per partition).
            "enable.idempotence": True,
            "acks": "all",  # implied by idempotence; written out because it matters
            # Same key-to-partition mapping as the Java client, so a Java and a
            # Python producer agree on where S-05 lives.
            "partitioner": "murmur2_random",
            "linger.ms": 20,  # wait up to 20 ms to batch: fewer, larger requests
            "compression.type": "lz4",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulated soil-moisture sensors -> Kafka.")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between live rounds")
    parser.add_argument("--rounds", type=int, help="stop after this many live rounds")
    parser.add_argument("--history-days", type=int, help="send past readings, then stop")
    parser.add_argument("--step-minutes", type=int, default=10, help="spacing of past readings")
    parser.add_argument("--seed", type=int, help="random seed, for reproducible runs")
    parser.add_argument("--poison", action="store_true", help="send one malformed message")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    out = ReadingProducer(new_producer(), sensor_topic())
    if args.poison:
        # No event_id, no ts, moisture above 100: the consumer must park it, not choke on it.
        out.send("S-99", json.dumps({"sensor_id": "S-99", "soil_moisture_pct": 140}).encode())
        out.flush()
        print(f"sent 1 poison message to {out.topic} (delivered={out.delivered})")
        return 0

    rng = random.Random(args.seed)
    fleet = build_fleet(load_sensors(), rng)

    if args.history_days:
        now = datetime.now(UTC)
        for at in history_timestamps(args.history_days, timedelta(minutes=args.step_minutes), now):
            for sim in fleet:
                out.send(sim.sensor.sensor_id, serialize(make_reading(sim, at, rng)))
        out.flush()
        print(f"history: delivered {out.delivered} readings, {out.failed} failed")
        return 0 if out.failed == 0 else 1

    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    rounds = 0
    while not stop and (args.rounds is None or rounds < args.rounds):
        at = datetime.now(UTC)
        for sim in fleet:
            out.send(sim.sensor.sensor_id, serialize(make_reading(sim, at, rng)))
        rounds += 1
        if rounds % 30 == 0:
            log.info("%d rounds, %d readings delivered", rounds, out.delivered)
        time.sleep(args.interval)
    out.flush()
    print(f"live: {rounds} rounds, delivered {out.delivered} readings, {out.failed} failed")
    return 0 if out.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
