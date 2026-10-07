"""Day 7: metrics and health endpoints for the sensor consumer, on one small HTTP server.

    /metrics   Prometheus text format (scraped every 15 s)
    /healthz   liveness: is the poll loop still turning? If not, Kubernetes restarts the pod.
    /ready     readiness: has it connected and polled at least once? If not, the pod is
               kept out of the Service until it is, but it is not restarted.

The metrics are the RED method for a stream consumer:
    Rate      messages_processed_total{outcome="stored|duplicate|skipped|dlq"}
    Errors    dlq_messages_total (and the outcome label above)
    Duration  processing_seconds (a histogram, so any percentile can be computed later)
Consumer lag comes from kafka-exporter, which reads it from Kafka itself.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# prometheus_client adds the _total suffix to counters when it exports them.
MESSAGES = Counter(
    "messages_processed", "Messages handled by the sensor consumer, by outcome", ["outcome"]
)
DLQ_MESSAGES = Counter("dlq_messages", "Messages parked in the dead-letter topic")
PROCESSING = Histogram(
    "processing_seconds",
    "Time to handle one message: validate, dedupe, write, commit",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


class Health:
    """Shared between the poll loop (which updates it) and the HTTP server (which reads it)."""

    def __init__(self, stale_after_seconds: float = 30.0) -> None:
        self.stale_after = stale_after_seconds
        self.last_beat = time.monotonic()
        self.ready = False

    def beat(self) -> None:
        self.last_beat = time.monotonic()

    @property
    def alive(self) -> bool:
        # poll() returns at least every second, so 30 s of silence means a stuck loop.
        return time.monotonic() - self.last_beat < self.stale_after


def record(outcome: str, seconds: float) -> None:
    MESSAGES.labels(outcome).inc()
    PROCESSING.observe(seconds)
    if outcome == "dlq":
        DLQ_MESSAGES.inc()


def serve(port: int, health: Health) -> ThreadingHTTPServer:
    """Start the endpoints on a background thread and return the server."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # probes every 5 s: too noisy
            pass

        def _reply(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/metrics":
                self._reply(200, generate_latest(), CONTENT_TYPE_LATEST)
            elif self.path == "/healthz":
                self._reply(*((200, b"ok") if health.alive else (503, b"poll loop stalled")))
            elif self.path == "/ready":
                self._reply(*((200, b"ready") if health.ready else (503, b"starting")))
            else:
                self._reply(404, b"not found")

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="metrics").start()
    return server
