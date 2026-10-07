"""Data contracts: the shape every record must have before it touches the database.

Validating at the boundary means bad data fails loudly here, with a clear message,
instead of slipping into raw tables and showing up days later as a wrong answer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Location(BaseModel):
    """One farm, as listed in seeds/locations.csv."""

    model_config = ConfigDict(frozen=True)

    location_id: int
    name: str
    region: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str  # IANA name, e.g. Asia/Kolkata. "Day" always means the farm's local day.


class WeatherDay(BaseModel):
    """One location, one local calendar day of weather."""

    # Contracts are values: once validated, nobody edits them.
    model_config = ConfigDict(frozen=True)

    location_id: int
    day: date
    t_max_c: float = Field(ge=-60, le=60)
    t_min_c: float = Field(ge=-60, le=60)
    # Required on purpose. If the API returns null for rain, validation fails and
    # the day is rejected, instead of quietly becoming "0 mm" (a dry day that
    # could trigger irrigation).
    rain_mm: float = Field(ge=0)
    et0_mm: float | None = Field(default=None, ge=0)  # FAO reference evapotranspiration

    @model_validator(mode="after")
    def min_not_above_max(self) -> Self:
        if self.t_min_c > self.t_max_c:
            raise ValueError(f"t_min_c {self.t_min_c} is above t_max_c {self.t_max_c}")
        return self


# ---------------------------------------------------------------- Day 2: sensors


class Sensor(BaseModel):
    """One soil-moisture sensor, as listed in transform/seeds/sensors.csv."""

    model_config = ConfigDict(frozen=True)

    sensor_id: str = Field(pattern=r"^S-\d{2}$")
    field_id: str = Field(pattern=r"^F-\d{2}$")
    field_name: str
    crop: str
    location_id: int


class SensorReading(BaseModel):
    """One reading on the Kafka topic sensor.readings (the message value, as JSON).

    Unknown extra fields are ignored, not rejected (Pydantic's default). That is
    what makes "add an optional field" a safe, backward-compatible change: a
    producer can start sending it before every consumer knows about it.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1, max_length=80)
    sensor_id: str = Field(pattern=r"^S-\d{2}$")
    # Event time, sent as Unix seconds. Pydantic turns a number into a UTC datetime,
    # and AwareDatetime refuses a timestamp with no timezone.
    ts: AwareDatetime
    soil_moisture_pct: float = Field(ge=0, le=100)

    @field_validator("ts")
    @classmethod
    def not_in_the_future(cls, ts: datetime) -> datetime:
        if ts > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("timestamp is more than 5 minutes ahead: is the sensor clock wrong?")
        return ts
