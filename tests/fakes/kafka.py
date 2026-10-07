"""Small in-memory stand-ins for the Kafka and Redis clients the consumer uses.

They implement only the methods SensorLoader calls, and they record what
happened (commits, DLQ messages) so tests can assert on it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeMessage:
    _value: bytes | None
    _key: bytes | None = b"S-05"
    _partition: int = 3
    _offset: int = 4182
    _topic: str = "sensor.readings"
    _headers: list[tuple[str, bytes]] | None = None

    def value(self) -> bytes | None:
        return self._value

    def key(self) -> bytes | None:
        return self._key

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def topic(self) -> str:
        return self._topic

    def headers(self) -> list[tuple[str, bytes]] | None:
        return self._headers

    def error(self) -> None:
        return None


@dataclass
class FakeConsumer:
    queue: deque[FakeMessage] = field(default_factory=deque)
    committed: list[tuple[int, int]] = field(default_factory=list)  # (partition, next offset)

    def poll(self, timeout: float) -> FakeMessage | None:
        return self.queue.popleft() if self.queue else None

    def commit(self, message: FakeMessage, asynchronous: bool = True) -> None:
        self.committed.append((message.partition(), message.offset() + 1))


@dataclass
class FakeProducer:
    sent: list[dict[str, Any]] = field(default_factory=list)
    unconfirmed: int = 0  # set > 0 to simulate a broker that never acknowledges

    def produce(self, topic: str, key: Any = None, value: Any = None, headers: Any = None) -> None:
        self.sent.append(
            {"topic": topic, "key": key, "value": value, "headers": dict(headers or [])}
        )

    def flush(self, timeout: float = -1) -> int:
        return self.unconfirmed


class FakeRedis:
    def __init__(self, down: bool = False) -> None:
        self.store: dict[str, Any] = {}
        self.down = down

    def _check(self) -> None:
        if self.down:
            import redis

            raise redis.ConnectionError("fake redis is down")

    def exists(self, key: str) -> int:
        self._check()
        return int(key in self.store)

    def pipeline(self) -> _FakePipeline:
        self._check()
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, parent: FakeRedis) -> None:
        self.parent = parent
        self.ops: list[tuple[str, Any]] = []

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.ops.append((key, value))

    def hset(self, name: str, mapping: dict[str, Any]) -> None:
        self.ops.append((name, dict(mapping)))

    def execute(self) -> None:
        for key, value in self.ops:
            self.parent.store[key] = value
