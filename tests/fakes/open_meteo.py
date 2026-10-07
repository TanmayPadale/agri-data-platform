"""A stand-in for the Open-Meteo API that replays recorded responses.

Why it exists: the free Open-Meteo API allows a fixed number of calls per IP per
day. Shared IPs (CI runners, cloud sandboxes) can use that up, and then every
fetch fails with HTTP 429. Tests and offline runs point the loader here instead:

    uv run python -m tests.fakes.open_meteo --port 8090
    export OPEN_METEO_ARCHIVE_URL=http://localhost:8090/v1/archive
    export OPEN_METEO_FORECAST_URL=http://localhost:8090/v1/forecast

It serves both farms for the days recorded in tests/fixtures/open_meteo_recorded/
(real responses, recorded 7 Oct 2026). `--fail-rate 0.3` makes 30% of requests
return 503, which is the Day 4 "watch the retries" lab.

Weather data by Open-Meteo.com, licensed CC BY 4.0.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ingest.weather import load_locations

RECORDED = Path(__file__).resolve().parent.parent / "fixtures" / "open_meteo_recorded"


def load_recordings(folder: Path = RECORDED) -> dict[int, dict[str, dict[str, Any]]]:
    """{location_id: {"YYYY-MM-DD": {variable: value}}} from every recorded file."""
    by_location: dict[int, dict[str, dict[str, Any]]] = {}
    for path in sorted(folder.glob("*.json")):
        location_id = int(path.stem.split("_")[0])
        daily = json.loads(path.read_text())["daily"]
        days = by_location.setdefault(location_id, {})
        for i, day in enumerate(daily["time"]):
            days[day] = {var: values[i] for var, values in daily.items() if var != "time"}
    return by_location


class FakeOpenMeteo(ThreadingHTTPServer):
    def __init__(self, port: int = 0, fail_rate: float = 0.0, host: str = "127.0.0.1") -> None:
        super().__init__((host, port), _Handler)
        self.recordings = load_recordings()
        self.locations = load_locations()
        self.fail_rate = fail_rate
        self.requests_served = 0

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.server_address[1]}"

    def start_in_thread(self) -> FakeOpenMeteo:
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self


class _Handler(BaseHTTPRequestHandler):
    server: FakeOpenMeteo

    def log_message(self, format: str, *args: Any) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self.server.requests_served += 1
        url = urlparse(self.path)
        if url.path not in ("/v1/archive", "/v1/forecast"):
            return self._send(404, {"error": True, "reason": f"unknown path {url.path}"})
        if random.random() < self.server.fail_rate:
            return self._send(503, {"error": True, "reason": "injected failure (--fail-rate)"})

        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        lat, lon = float(query["latitude"]), float(query["longitude"])
        location = next(
            (
                loc
                for loc in self.server.locations
                if abs(loc.latitude - lat) < 0.01 and abs(loc.longitude - lon) < 0.01
            ),
            None,
        )
        if location is None:
            return self._send(400, {"error": True, "reason": "no recording for these coordinates"})

        start, end = date.fromisoformat(query["start_date"]), date.fromisoformat(query["end_date"])
        variables = query["daily"].split(",")
        recorded = self.server.recordings.get(location.location_id, {})
        days = [
            date.fromordinal(o).isoformat() for o in range(start.toordinal(), end.toordinal() + 1)
        ]
        missing = [d for d in days if d not in recorded]
        if missing:
            # The real API also answers 400 for dates it cannot serve.
            return self._send(400, {"error": True, "reason": f"not recorded: {missing[0]}..."})

        daily: dict[str, list[Any]] = {"time": days}
        for var in variables:
            daily[var] = [recorded[d].get(var) for d in days]
        self._send(
            200,
            {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "timezone": query.get("timezone", "GMT"),
                "daily": daily,
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--fail-rate", type=float, default=0.0)
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 lets containers reach it")
    args = parser.parse_args()
    server = FakeOpenMeteo(args.port, args.fail_rate, args.host)
    print(f"fake Open-Meteo on {server.base_url} (fail rate {args.fail_rate:.0%})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
