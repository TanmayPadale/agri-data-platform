"""The weather Lambda against moto's in-memory S3: no AWS account, no Docker, no cost."""

import csv
import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

REPO = Path(__file__).resolve().parent.parent
HANDLER = REPO / "infra" / "lambda" / "weather_to_s3" / "handler.py"
BUCKET = "agri-raw-test"


def load_handler():
    # "lambda" is a Python keyword, so the folder cannot be imported by dotted path.
    # Load the file directly instead, exactly as the Lambda runtime does.
    spec = importlib.util.spec_from_file_location("weather_to_s3_handler", HANDLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def s3(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "ap-southeast-2",
        "BUCKET": BUCKET,
        "LOCATIONS_JSON": json.dumps(list(csv.DictReader((REPO / "seeds/locations.csv").open()))),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        client = boto3.client("s3", region_name="ap-southeast-2")
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"}
        )
        yield client


def fake_fetch(location, day):
    return {
        "daily": {"time": [day.isoformat()], "precipitation_sum": [0.4]},
        "loc": location["name"],
    }


def test_writes_one_object_per_farm_under_the_day_partition(s3, monkeypatch):
    handler = load_handler()
    monkeypatch.setattr(handler, "fetch_day", fake_fetch)
    result = handler.handler({"day": "2026-10-01"})
    assert result["written"] == [
        "weather/dt=2026-10-01/location_id=1/open_meteo.json",
        "weather/dt=2026-10-01/location_id=2/open_meteo.json",
    ]
    body = json.loads(s3.get_object(Bucket=BUCKET, Key=result["written"][1])["Body"].read())
    assert body["loc"] == "Griffith"


def test_rerun_overwrites_instead_of_adding(s3, monkeypatch):
    handler = load_handler()
    monkeypatch.setattr(handler, "fetch_day", fake_fetch)
    handler.handler({"day": "2026-10-01"})
    handler.handler({"day": "2026-10-01"})
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert len(keys) == 2


def test_default_day_is_each_farms_own_yesterday():
    handler = load_handler()
    # The schedule fires at 20:30 UTC on 1 Oct: already 2 Oct in Jawali and in Griffith.
    fired = datetime(2026, 10, 1, 20, 30, tzinfo=UTC)
    assert handler.local_yesterday("Asia/Kolkata", fired) == date(2026, 10, 1)
    assert handler.local_yesterday("Australia/Sydney", fired) == date(2026, 10, 1)
