"""Application configuration.

Config is validated at startup via pydantic; invalid config fails fast with a
clear message rather than surfacing as a confusing runtime error later.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PointBufferConfig(BaseModel):
    """How a Point input is turned into an area (see geo/buffer.py)."""

    strategy: Literal["geodesic_circle"] = "geodesic_circle"
    default_radius_m: float = 10.0
    min_radius_m: float = 1.0
    max_radius_m: float = 5_000.0
    quad_segs: int = 16
    allow_request_override: bool = False

    @model_validator(mode="after")
    def _check_radius_bounds(self) -> "PointBufferConfig":
        if not (0 < self.min_radius_m <= self.default_radius_m <= self.max_radius_m):
            raise ValueError(
                "point_buffer radii must satisfy "
                "0 < min_radius_m <= default_radius_m <= max_radius_m, got "
                f"min={self.min_radius_m}, default={self.default_radius_m}, "
                f"max={self.max_radius_m}"
            )
        if self.quad_segs < 4:
            raise ValueError(f"point_buffer.quad_segs must be >= 4, got {self.quad_segs}")
        return self


class LimitsConfig(BaseModel):
    """Bounds used by request/checks.py to reject oversized or malformed input."""

    max_wkt_bytes: int = 100_000
    max_vertices: int = 10_000
    max_area_km2: float = 100.0
    max_prompt_chars: int = 2_000

    @model_validator(mode="after")
    def _check_positive(self) -> "LimitsConfig":
        for name in ("max_wkt_bytes", "max_vertices", "max_area_km2", "max_prompt_chars"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"limits.{name} must be > 0, got {value}")
        return self


class AreaStoreConfig(BaseModel):
    """Bounds for the in-memory area store (see geo/area_store.py)."""

    max_entries: int = 10_000

    @model_validator(mode="after")
    def _check_positive(self) -> "AreaStoreConfig":
        if self.max_entries <= 0:
            raise ValueError(f"area_store.max_entries must be > 0, got {self.max_entries}")
        return self


class GeoConfig(BaseSettings):
    """Root config. Env prefix GEOSEARCH_, nested delimiter __.

    Example override: GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M=1000
    """

    model_config = SettingsConfigDict(env_prefix="GEOSEARCH_", env_nested_delimiter="__")

    point_buffer: PointBufferConfig = Field(default_factory=PointBufferConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    area_store: AreaStoreConfig = Field(default_factory=AreaStoreConfig)
