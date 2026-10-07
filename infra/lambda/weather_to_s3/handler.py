"""Day 5: AWS Lambda that lands each farm's latest complete day of weather in S3.

EventBridge runs it daily at 20:30 UTC. For each farm it works out "yesterday" in
the farm's own timezone, fetches that day from Open-Meteo and writes:

    s3://<BUCKET>/weather/dt=YYYY-MM-DD/location_id=<n>/open_meteo.json

The same day always maps to the same key, so a retry or a manual rerun overwrites
the object instead of adding a copy (and versioning keeps the previous one).

Only the standard library and boto3 are used. The Lambda Python runtime already
includes boto3, so the deployment package is just this one file.

Run it locally against the emulator (make tf-emulator-run does exactly this):
    AWS_ENDPOINT_URL=http://localhost:4566 BUCKET=agri-raw-local \\
    LOCATIONS_CSV=seeds/locations.csv python infra/lambda/weather_to_s3/handler.py
"""

from __future__ import annotations

import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import boto3

DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration"


def forecast_url() -> str:
    return os.environ.get("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")


def local_yesterday(timezone: str, now: datetime) -> date:
    return now.astimezone(ZoneInfo(timezone)).date() - timedelta(days=1)


def fetch_day(location: dict[str, str], day: date) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "daily": DAILY,
            "timezone": location["timezone"],
        }
    )
    with urllib.request.urlopen(f"{forecast_url()}?{query}", timeout=20) as resp:
        return json.load(resp)


def s3_key(day: date, location_id: str) -> str:
    return f"weather/dt={day.isoformat()}/location_id={location_id}/open_meteo.json"


def handler(event: dict[str, Any] | None, context: Any = None) -> dict[str, Any]:
    """Lambda entry point. Pass {"day": "YYYY-MM-DD"} to load a specific day instead."""
    bucket = os.environ["BUCKET"]
    if "LOCATIONS_JSON" in os.environ:  # set by Terraform from seeds/locations.csv
        locations = json.loads(os.environ["LOCATIONS_JSON"])
    else:  # local runs read the seed file directly
        with open(os.environ.get("LOCATIONS_CSV", "seeds/locations.csv"), newline="") as f:
            locations = list(csv.DictReader(f))
    now = datetime.now(UTC)
    fixed_day = date.fromisoformat(event["day"]) if event and event.get("day") else None

    s3 = boto3.client("s3")  # honours AWS_ENDPOINT_URL, so the same code runs on the emulator
    written = []
    for loc in locations:
        day = fixed_day or local_yesterday(loc["timezone"], now)
        payload = fetch_day(loc, day)
        key = s3_key(day, loc["location_id"])
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(payload).encode(),
            ContentType="application/json",
        )
        written.append(key)
        print(json.dumps({"msg": "wrote", "bucket": bucket, "key": key}))  # one JSON log line
    return {"written": written}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the Lambda handler locally.")
    parser.add_argument("--day", help="YYYY-MM-DD (default: each farm's local yesterday)")
    args = parser.parse_args()
    print(handler({"day": args.day} if args.day else {}))
