"""Data contracts: the shape every record must have before it touches the database.

Validating at the boundary means bad data fails loudly here, with a clear message,
instead of slipping into raw tables and showing up days later as a wrong answer.
"""

from __future__ import annotations

from datetime import date
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
