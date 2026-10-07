"""Weather fetch and parse, without network or database.

The HTTP layer is faked with httpx.MockTransport, so these tests run anywhere in
milliseconds and never spend the free Open-Meteo quota.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from ingest.weather import (
    day_path,
    fetch_day_to_file,
    fetch_range_to_files,
    latest_complete_day,
    load_locations,
    parse_daily,
    split_by_endpoint,
)

FIXTURE = Path(__file__).parent / "fixtures" / "open_meteo_archive_jawali.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def jawali():
    return [loc for loc in load_locations() if loc.location_id == 1]


def test_parse_saved_response(payload):
    rows, rejected = parse_daily(1, payload["daily"])
    assert rejected == []
    assert len(rows) == 5
    first = rows[0]
    assert first.day == date(2026, 9, 24)
    assert first.t_max_c == pytest.approx(29.5)
    assert first.rain_mm == pytest.approx(0.4)


def test_null_rain_rejects_that_day_only(payload):
    daily = payload["daily"]
    daily["precipitation_sum"][2] = None
    rows, rejected = parse_daily(1, daily)
    assert len(rows) == 4
    assert [day for day, _ in rejected] == ["2026-09-26"]
    assert "rain_mm" in rejected[0][1]


def test_split_by_endpoint_uses_archive_for_settled_days():
    today = date(2026, 10, 7)
    assert split_by_endpoint(date(2026, 9, 1), date(2026, 9, 10), today) == [
        ("archive", date(2026, 9, 1), date(2026, 9, 10))
    ]
    assert split_by_endpoint(date(2026, 9, 28), date(2026, 10, 6), today) == [
        ("archive", date(2026, 9, 28), date(2026, 10, 2)),
        ("forecast", date(2026, 10, 3), date(2026, 10, 6)),
    ]


def test_latest_complete_day_waits_for_every_farm():
    farms = load_locations()
    # 20:30 UTC on 1 Oct is 02:00 on 2 Oct in Jawali and 06:30 on 2 Oct in Griffith,
    # so 1 Oct has ended at both farms.
    assert latest_complete_day(farms, datetime(2026, 10, 1, 20, 30, tzinfo=UTC)) == date(
        2026, 10, 1
    )
    # 12:00 UTC on 1 Oct is 17:30 on 1 Oct in Jawali, so 1 Oct is still running there
    # and the latest day complete everywhere is 30 Sep.
    assert latest_complete_day(farms, datetime(2026, 10, 1, 12, 0, tzinfo=UTC)) == date(2026, 9, 30)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_writes_one_deterministic_file_per_day(payload, jawali, isolated_data_dir):
    client = _mock_client(lambda request: httpx.Response(200, json=payload))
    paths = fetch_range_to_files(
        date(2026, 9, 24),
        date(2026, 9, 28),
        locations=jawali,
        client=client,
        today=date(2026, 10, 7),
    )
    assert paths == [day_path(date(2026, 9, d)) for d in range(24, 29)]
    assert paths[0] == isolated_data_dir / "raw/weather/dt=2026-09-24/open_meteo.json"
    doc = json.loads(paths[0].read_text())
    assert doc["locations"][0]["endpoint"] == "archive"
    assert doc["locations"][0]["daily"]["time"] == ["2026-09-24"]


def test_rerun_overwrites_the_same_file(payload, jawali):
    client = _mock_client(lambda request: httpx.Response(200, json=payload))
    kwargs = {"locations": jawali, "client": client, "today": date(2026, 10, 7)}
    first = fetch_range_to_files(date(2026, 9, 24), date(2026, 9, 28), **kwargs)
    second = fetch_range_to_files(date(2026, 9, 24), date(2026, 9, 28), **kwargs)
    assert first == second
    assert len(list(first[0].parent.iterdir())) == 1  # no leftover temp files either


def test_transient_errors_are_retried(payload, jawali, monkeypatch):
    monkeypatch.setattr("ingest.retry.time.sleep", lambda seconds: None)
    calls = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=payload)

    path = fetch_day_to_file(
        date(2026, 9, 24), locations=jawali, client=_mock_client(flaky), today=date(2026, 10, 7)
    )
    assert len(calls) == 3  # failed, failed, succeeded
    assert path.exists()


def test_bad_request_is_not_retried(jawali, monkeypatch):
    monkeypatch.setattr("ingest.retry.time.sleep", lambda seconds: None)
    calls = []

    def bad(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": True, "reason": "bad parameter"})

    with pytest.raises(httpx.HTTPStatusError):
        fetch_day_to_file(
            date(2026, 9, 24), locations=jawali, client=_mock_client(bad), today=date(2026, 10, 7)
        )
    assert len(calls) == 1  # a 400 is our bug; retrying cannot fix it


def test_request_asks_for_the_farm_local_day(jawali, payload):
    seen = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=payload)

    fetch_range_to_files(
        date(2026, 9, 24),
        date(2026, 9, 28),
        locations=jawali,
        client=_mock_client(capture),
        today=date(2026, 10, 7),
    )
    assert seen["timezone"] == "Asia/Kolkata"
    assert seen["start_date"] == "2026-09-24"
    assert "precipitation_sum" in seen["daily"]
