"""Point buffering: the only geographic transform this system is allowed to
make (see CLAUDE.md invariant 2), and the resolution of what radius to use.
"""

from collections.abc import Callable
from typing import Protocol

from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform

from geosearch.config import PointBufferConfig
from geosearch.errors import ErrorCode, GeoValidationError


class BufferStrategy(Protocol):
    """Anything that can turn a Point into an area. Adding a new strategy means
    one new class plus one entry in STRATEGIES; nothing else changes."""

    name: str

    def buffer(self, point: Point, radius_m: float) -> Polygon: ...


class GeodesicCircleBuffer:
    """Buffers a Point into a true circle of radius_m metres, anywhere on Earth.

    Buffering directly in degrees is wrong: a degree of longitude shrinks
    toward the poles by a factor of cos(latitude), while a degree of latitude
    stays roughly constant. A degree-sized buffer therefore produces an
    ellipse whose size in metres depends on where on Earth the point sits —
    the same "radius" in degrees could be 1km near the equator and a few
    hundred metres near the poles in the east-west direction.

    Instead we build a local azimuthal equidistant (AEQD) projection centred
    exactly on the point, where distance from the center is exact in metres
    in every direction by construction, buffer there, and project back.
    """

    name = "geodesic_circle"

    def __init__(self, quad_segs: int):
        self.quad_segs = quad_segs

    def buffer(self, point: Point, radius_m: float) -> Polygon:
        aeqd = f"+proj=aeqd +lat_0={point.y} +lon_0={point.x} +datum=WGS84 +units=m +no_defs"
        to_local = Transformer.from_crs("EPSG:4326", aeqd, always_xy=True)
        to_geographic = Transformer.from_crs(aeqd, "EPSG:4326", always_xy=True)

        local_point = transform(to_local.transform, point)
        local_circle = local_point.buffer(radius_m, quad_segs=self.quad_segs)
        return transform(to_geographic.transform, local_circle)


STRATEGIES: dict[str, Callable[[PointBufferConfig], BufferStrategy]] = {
    "geodesic_circle": lambda cfg: GeodesicCircleBuffer(cfg.quad_segs),
}


def resolve_buffer_radius(requested: float | None, cfg: PointBufferConfig) -> float:
    """The single rule for what radius a Point gets buffered with (spec section 8.4)."""
    if requested is None:
        return cfg.default_radius_m
    if not cfg.allow_request_override:
        raise GeoValidationError(
            ErrorCode.BUFFER_OVERRIDE_NOT_ALLOWED,
            "point_buffer_m was supplied but overriding the buffer radius is not allowed",
            {"requested": requested},
        )
    if not (cfg.min_radius_m <= requested <= cfg.max_radius_m):
        raise GeoValidationError(
            ErrorCode.BUFFER_RADIUS_OUT_OF_RANGE,
            f"point_buffer_m must be between {cfg.min_radius_m} and {cfg.max_radius_m} metres",
            {
                "requested": requested,
                "min_radius_m": cfg.min_radius_m,
                "max_radius_m": cfg.max_radius_m,
            },
        )
    return requested
