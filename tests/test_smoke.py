import csv
from pathlib import Path


def test_locations_seed_has_both_farms():
    rows = list(csv.DictReader(Path("seeds/locations.csv").open()))
    assert {r["location_id"] for r in rows} == {"1", "2"}
