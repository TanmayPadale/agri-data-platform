from datetime import date
from pydantic import BaseModel, Field

class WeatherDay(BaseModel):
    location_id: int
    day: date
    t_max_c: float = Field(ge=-60, le=60)
    rain_mm: float = Field(ge=0)