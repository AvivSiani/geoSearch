"""Scale fixture: ten fake tools with distinct descriptions and canned results.

For tests and evals only. This module is deliberately NOT in `MODULES`
(sources/__init__.py), so its handlers are never on the production allowlist;
callers register them into a registry of their choosing with `register_fakes`.

Why it exists: progressive disclosure is only meaningful when the catalog has
many tools the agent must *not* load. These give the agent realistic,
clearly-distinct descriptions to choose between, and give the token ledger
real schemas to (not) pay for. Results are canned and deterministic.
"""

from pydantic import BaseModel, Field

from geosearch.registry.handlers import HandlerContext, HandlerRegistry, ToolResult
from geosearch.registry.models import ToolDefinition


class NoInput(BaseModel):
    pass


class HoursInput(BaseModel):
    hours: int = Field(default=12, ge=1, le=48, description="How many hours ahead.")


class DaysInput(BaseModel):
    days: int = Field(default=7, ge=1, le=14, description="How many days ahead.")


class DateInput(BaseModel):
    date: str = Field(default="today", description="Date as YYYY-MM-DD, or 'today'.")


class WeatherNow(BaseModel):
    temperature_c: float
    conditions: str
    wind_kph: float


class ForecastRow(BaseModel):
    hour: int
    temperature_c: float
    precip_mm: float


class EventRow(BaseModel):
    name: str
    date: str
    venue: str


class TrafficRow(BaseModel):
    road: str
    congestion: float
    delay_min: int


class AirQuality(BaseModel):
    aqi: int
    main_pollutant: str


class StopRow(BaseModel):
    name: str
    kind: str
    lon: float
    lat: float


class ParkingRow(BaseModel):
    name: str
    free_spaces: int
    lon: float
    lat: float


class SunTimes(BaseModel):
    sunrise: str
    sunset: str


class Elevation(BaseModel):
    min_m: float
    max_m: float
    mean_m: float


class Population(BaseModel):
    population: int
    density_per_km2: float


def _weather_now(args: NoInput, ctx: HandlerContext) -> ToolResult:
    return ToolResult(
        summary="Current weather: 24°C, clear, light wind.",
        data={"temperature_c": 24.0, "conditions": "clear", "wind_kph": 9.0},
    )


def _forecast(args: HoursInput, ctx: HandlerContext) -> ToolResult:
    rows = [
        {"hour": h, "temperature_c": 24.0 - h * 0.3, "precip_mm": 0.0} for h in range(args.hours)
    ]
    return ToolResult(summary=f"Hourly forecast for the next {args.hours} hours.", artifact=rows)


def _events(args: DaysInput, ctx: HandlerContext) -> ToolResult:
    rows = [
        {"name": "Jazz in the Park", "date": "2026-10-08", "venue": "City Park"},
        {"name": "Food Market", "date": "2026-10-10", "venue": "Harbor Square"},
    ]
    return ToolResult(summary=f"{len(rows)} events in the next {args.days} days.", artifact=rows)


def _traffic(args: NoInput, ctx: HandlerContext) -> ToolResult:
    rows = [{"road": "Main St", "congestion": 0.7, "delay_min": 6}]
    return ToolResult(summary="Moderate congestion on 1 road.", artifact=rows)


def _air_quality(args: NoInput, ctx: HandlerContext) -> ToolResult:
    return ToolResult(summary="Air quality is good.", data={"aqi": 42, "main_pollutant": "pm2.5"})


def _transit(args: NoInput, ctx: HandlerContext) -> ToolResult:
    rows = [{"name": "Central Station", "kind": "rail", "lon": 34.78, "lat": 32.08}]
    return ToolResult(summary="1 transit stop.", artifact=rows)


def _parking(args: NoInput, ctx: HandlerContext) -> ToolResult:
    rows = [{"name": "Lot A", "free_spaces": 35, "lon": 34.77, "lat": 32.07}]
    return ToolResult(summary="1 parking lot with free spaces.", artifact=rows)


def _sun_times(args: DateInput, ctx: HandlerContext) -> ToolResult:
    return ToolResult(
        summary=f"Sun times for {args.date}.", data={"sunrise": "06:31", "sunset": "18:02"}
    )


def _elevation(args: NoInput, ctx: HandlerContext) -> ToolResult:
    return ToolResult(
        summary="Ground elevation 2–48 m.", data={"min_m": 2.0, "max_m": 48.0, "mean_m": 21.5}
    )


def _population(args: NoInput, ctx: HandlerContext) -> ToolResult:
    return ToolResult(
        summary="About 61,000 residents.", data={"population": 61000, "density_per_km2": 2330.0}
    )


# source_id -> (description, input model, output model, handler)
FAKES = {
    101: ("Current weather at the area: temperature, conditions and wind.",
          NoInput, WeatherNow, _weather_now),
    102: ("Hourly weather forecast for the area for the next N hours.",
          HoursInput, ForecastRow, _forecast),
    103: ("Upcoming public events (concerts, festivals, markets) in the area.",
          DaysInput, EventRow, _events),
    104: ("Current road traffic congestion and delays in the area.",
          NoInput, TrafficRow, _traffic),
    105: ("Current air quality index (AQI) and main pollutant in the area.",
          NoInput, AirQuality, _air_quality),
    106: ("Public transit stops (bus, rail) inside the area.",
          NoInput, StopRow, _transit),
    107: ("Parking lots inside the area and their free spaces.",
          NoInput, ParkingRow, _parking),
    108: ("Sunrise and sunset times at the area for a date.",
          DateInput, SunTimes, _sun_times),
    109: ("Ground elevation in the area: minimum, maximum and mean, in meters.",
          NoInput, Elevation, _elevation),
    110: ("Estimated resident population and population density of the area.",
          NoInput, Population, _population),
}  # fmt: skip

WEATHER_NOW = 101
FAKE_DEFINITIONS = [
    ToolDefinition(source_id=source_id, description=description)
    for source_id, (description, *_rest) in FAKES.items()
]


def register_fakes(handler_registry: HandlerRegistry) -> HandlerRegistry:
    """Register every fake into `handler_registry` (all use the area)."""
    for source_id, (_description, input_model, output_model, func) in FAKES.items():
        handler_registry.register(
            source_id=source_id, input_model=input_model, output_model=output_model, uses_area=True
        )(func)
    return handler_registry
