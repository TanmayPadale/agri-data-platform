"""Day 7: the consumer's /metrics, /healthz and /ready endpoints. No Kafka needed."""

import httpx
import pytest

from ingest import observability


@pytest.fixture
def endpoints():
    health = observability.Health(stale_after_seconds=30)
    server = observability.serve(0, health)  # port 0: the OS picks a free port
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield health, base
    server.shutdown()


def test_ready_only_after_joining_the_group(endpoints):
    health, base = endpoints
    assert httpx.get(f"{base}/ready").status_code == 503  # starting: kept out of the Service
    health.ready = True  # what on_assign does
    assert httpx.get(f"{base}/ready").status_code == 200


def test_liveness_fails_when_the_loop_stops_beating(endpoints):
    health, base = endpoints
    assert httpx.get(f"{base}/healthz").status_code == 200
    health.stale_after = 0  # pretend 30 s passed without a poll
    assert httpx.get(f"{base}/healthz").status_code == 503  # Kubernetes would restart the pod


def test_metrics_count_outcomes(endpoints):
    _, base = endpoints

    def value(text: str, line_start: str) -> float:
        lines = [ln for ln in text.splitlines() if ln.startswith(line_start)]
        return float(lines[0].split()[-1]) if lines else 0.0

    before = httpx.get(f"{base}/metrics").text
    observability.record("stored", 0.004)
    observability.record("dlq", 0.002)
    after = httpx.get(f"{base}/metrics").text

    stored = 'messages_processed_total{outcome="stored"}'
    assert value(after, stored) == value(before, stored) + 1
    assert value(after, "dlq_messages_total") == value(before, "dlq_messages_total") + 1
    assert value(after, "processing_seconds_count") == value(before, "processing_seconds_count") + 2


def test_unknown_paths_are_404(endpoints):
    _, base = endpoints
    assert httpx.get(f"{base}/nope").status_code == 404
