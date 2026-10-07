"""Day 1: daily weather from Open-Meteo into raw.weather_daily.

The flow, one day at a time:

    fetch_day_to_file(day)  ->  data/raw/weather/dt=YYYY-MM-DD/open_meteo.json
    upsert_file(path)       ->  rows upserted into raw.weather_daily

Splitting "fetch" from "load" with a file in between is deliberate. The file is a
receipt of exactly what the API said, a rerun of the load needs no network, and
Airflow (Day 4) passes a short file path between tasks instead of the data itself.

Run it:

    uv run python -m ingest.weather --days 90       # history for both farms
    uv run python -m ingest.weather --day 2026-10-01
    uv run python -m ingest.weather --yesterday     # what the daily jobs do
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
from pydantic import ValidationError

from ingest.config import REPO_ROOT, data_dir
from ingest.db import connect
from ingest.models import Location, WeatherDay
from ingest.retry import retry

log = logging.getLogger(__name__)

LOCATIONS_CSV = REPO_ROOT / "seeds" / "locations.csv"

# Two endpoints serve the same daily variables. Their URLs can be overridden with
# OPEN_METEO_ARCHIVE_URL / OPEN_METEO_FORECAST_URL, which is how tests and offline
# runs point at the fake server in tests/fakes/open_meteo.py.
DEFAULT_URLS = {
    "archive": "https://archive-api.open-meteo.com/v1/archive",
    "forecast": "https://api.open-meteo.com/v1/forecast",
}
DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "et0_fao_evapotranspiration",
]
# The archive (reanalysis) data settles a few days late. Anything newer comes from
# the forecast endpoint, which also serves the recent past.
ARCHIVE_LAG_DAYS = 5


class TransientHTTPError(Exception):
    """A response worth retrying: rate limited (429) or a server error (5xx)."""


# ---------------------------------------------------------------- locations


def load_locations(path: Path = LOCATIONS_CSV) -> list[Location]:
    with path.open(newline="") as f:
        return [Location.model_validate(row) for row in csv.DictReader(f)]


def latest_complete_day(locations: Iterable[Location], now: datetime | None = None) -> date:
    """The most recent calendar day that has already ended at every farm.

    Both farms are ahead of UTC (India +5:30, NSW +10 or +11), so their local
    "yesterday" is usually the UTC "today" late in the UTC evening. Asking each
    farm's own clock avoids loading a day that is still in progress somewhere.
    """
    now = now or datetime.now(UTC)
    return min(
        (now.astimezone(ZoneInfo(loc.timezone)).date() - timedelta(days=1)) for loc in locations
    )


# ---------------------------------------------------------------- fetch


def endpoint_url(kind: str) -> str:
    return os.environ.get(f"OPEN_METEO_{kind.upper()}_URL", DEFAULT_URLS[kind])


def split_by_endpoint(start: date, end: date, today: date) -> list[tuple[str, date, date]]:
    """Split the inclusive range [start, end] into at most two (kind, start, end) pieces."""
    cutoff = today - timedelta(days=ARCHIVE_LAG_DAYS)
    pieces = []
    if start <= cutoff:
        pieces.append(("archive", start, min(end, cutoff)))
    if end > cutoff:
        pieces.append(("forecast", max(start, cutoff + timedelta(days=1)), end))
    return pieces


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"{resp.status_code} from {resp.url}")
    # Any other 4xx means our request is wrong. Retrying it cannot help, so fail now.
    resp.raise_for_status()


@retry(times=4, retry_on=(httpx.TransportError, TransientHTTPError))
def fetch_daily(
    client: httpx.Client, url: str, location: Location, start: date, end: date
) -> dict[str, Any]:
    params: dict[str, str | float] = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_VARS),
        # Days are cut at local midnight, so "1 Oct" means 1 Oct on that farm.
        "timezone": location.timezone,
    }
    resp = client.get(url, params=params)
    _raise_for_status(resp)
    return resp.json()


def split_payload_by_day(daily: dict[str, list[Any]]) -> Iterator[dict[str, list[Any]]]:
    """Turn Open-Meteo's column arrays into one small `daily` block per day."""
    for i in range(len(daily["time"])):
        yield {key: [values[i]] for key, values in daily.items()}


@contextmanager
def _http_client(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Use the caller's client (tests pass a fake one), or open and close our own."""
    if client is not None:
        yield client
        return
    with httpx.Client(
        timeout=30, headers={"User-Agent": "agri-data-platform (learning project)"}
    ) as own:
        yield own


def day_path(day: date, root: Path | None = None) -> Path:
    """Deterministic: the same day always maps to the same file, so a rerun overwrites it."""
    return (root or data_dir()) / "raw" / "weather" / f"dt={day.isoformat()}" / "open_meteo.json"


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    """Write to a temp file, then rename. A crash mid-write never leaves half a file
    behind, because the rename is atomic on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=2))
    os.replace(tmp, path)


def fetch_range_to_files(
    start: date,
    end: date,
    *,
    locations: list[Location] | None = None,
    client: httpx.Client | None = None,
    today: date | None = None,
    root: Path | None = None,
) -> list[Path]:
    """Fetch [start, end] for every farm and write one file per day.

    One request per farm per endpoint, however long the range, then the response
    is split into daily files so a 90-day history and a 1-day daily run land in
    exactly the same layout.
    """
    locations = locations or load_locations()
    today = today or datetime.now(UTC).date()
    per_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    with _http_client(client) as http:
        for loc in locations:
            for kind, piece_start, piece_end in split_by_endpoint(start, end, today):
                payload = fetch_daily(http, endpoint_url(kind), loc, piece_start, piece_end)
                for one_day in split_payload_by_day(payload["daily"]):
                    day = date.fromisoformat(one_day["time"][0])
                    per_day[day].append(
                        {"location_id": loc.location_id, "endpoint": kind, "daily": one_day}
                    )

    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    paths = []
    for day in sorted(per_day):
        path = day_path(day, root)
        write_json_atomic(
            path, {"day": day.isoformat(), "fetched_at": fetched_at, "locations": per_day[day]}
        )
        paths.append(path)
    return paths


def fetch_day_to_file(day: date, **kwargs: Any) -> Path:
    paths = fetch_range_to_files(day, day, **kwargs)
    if not paths:
        raise RuntimeError(f"Open-Meteo returned no data for {day}")
    return paths[0]


# ---------------------------------------------------------------- validate + load


def parse_daily(
    location_id: int, daily: dict[str, list[Any]]
) -> tuple[list[WeatherDay], list[tuple[str, str]]]:
    """Validate every day in a `daily` block. Returns (good rows, rejected (day, reason))."""
    rows: list[WeatherDay] = []
    rejected: list[tuple[str, str]] = []
    for i, day in enumerate(daily["time"]):
        try:
            rows.append(
                WeatherDay(
                    location_id=location_id,
                    day=day,
                    t_max_c=daily["temperature_2m_max"][i],
                    t_min_c=daily["temperature_2m_min"][i],
                    rain_mm=daily["precipitation_sum"][i],
                    et0_mm=daily["et0_fao_evapotranspiration"][i],
                )
            )
        except ValidationError as exc:
            reasons = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            rejected.append((day, reasons))
    return rows, rejected


UPSERT_SQL = """
INSERT INTO raw.weather_daily (location_id, day, t_max_c, t_min_c, rain_mm, et0_mm, source)
VALUES (%(location_id)s, %(day)s, %(t_max_c)s, %(t_min_c)s, %(rain_mm)s, %(et0_mm)s, %(source)s)
ON CONFLICT (location_id, day) DO UPDATE
SET t_max_c   = EXCLUDED.t_max_c,
    t_min_c   = EXCLUDED.t_min_c,
    rain_mm   = EXCLUDED.rain_mm,
    et0_mm    = EXCLUDED.et0_mm,
    source    = EXCLUDED.source,
    loaded_at = now()
"""
# first_loaded_at is missing from the SET list on purpose: a reload must not move it.


def source_name(endpoint_kind: str) -> str:
    """Recorded on every row, so you can tell a settled archive value from a recent
    forecast-endpoint value that a later backfill will overwrite."""
    return f"open-meteo-{endpoint_kind}"


def upsert_rows(conn: psycopg.Connection, rows: list[WeatherDay], source: str) -> int:
    params = [row.model_dump() | {"source": source} for row in rows]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, params)
    return len(params)


def upsert_file(path: Path | str, *, dsn: str | None = None) -> int:
    """Load one day file. Returns how many rows were upserted.

    The whole file is one transaction: every farm's row lands, or none does.
    """
    document = json.loads(Path(path).read_text())
    total = 0
    with connect(dsn) as conn:
        for block in document["locations"]:
            rows, rejected = parse_daily(block["location_id"], block["daily"])
            for day, reason in rejected:
                log.warning("rejected location %s day %s: %s", block["location_id"], day, reason)
            total += upsert_rows(conn, rows, source_name(block["endpoint"]))
    return total


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Open-Meteo daily weather into Postgres.")
    when = parser.add_mutually_exclusive_group(required=True)
    when.add_argument("--days", type=int, help="the last N complete days")
    when.add_argument("--day", type=date.fromisoformat, help="one day, YYYY-MM-DD")
    when.add_argument("--start", type=date.fromisoformat, help="first day (use with --end)")
    when.add_argument(
        "--yesterday", action="store_true", help="the latest day that has ended at every farm"
    )
    parser.add_argument("--end", type=date.fromisoformat, help="last day, inclusive")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    locations = load_locations()
    last = latest_complete_day(locations)
    if args.days:
        start, end = last - timedelta(days=args.days - 1), last
    elif args.day:
        start = end = args.day
    elif args.start:
        start, end = args.start, args.end or last
    else:
        start = end = last

    paths = fetch_range_to_files(start, end, locations=locations)
    rows = sum(upsert_file(path) for path in paths)
    print(f"{start} to {end}: wrote {len(paths)} day files, upserted {rows} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
