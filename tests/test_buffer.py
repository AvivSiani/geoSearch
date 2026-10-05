import math

import pytest
from pyproj import Geod
from shapely.geometry import Point

from geosearch.config import PointBufferConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.geo.buffer import STRATEGIES, GeodesicCircleBuffer, resolve_buffer_radius

GEOD = Geod(ellps="WGS84")


@pytest.mark.parametrize("radius_m", [500.0, 5_000.0])
@pytest.mark.parametrize("lat", [0.0, 32.0, 60.0])
def test_geodesic_circle_vertex_distances_within_tolerance(radius_m: float, lat: float) -> None:
    point = Point(10.0, lat)
    circle = GeodesicCircleBuffer(quad_segs=16).buffer(point, radius_m)

    for lon, vlat in circle.exterior.coords:
        _, _, distance = GEOD.inv(point.x, point.y, lon, vlat)
        assert distance == pytest.approx(radius_m, rel=0.005)


@pytest.mark.parametrize("radius_m", [500.0, 5_000.0])
@pytest.mark.parametrize("lat", [0.0, 32.0, 60.0])
def test_geodesic_circle_area_within_tolerance(radius_m: float, lat: float) -> None:
    point = Point(10.0, lat)
    circle = GeodesicCircleBuffer(quad_segs=16).buffer(point, radius_m)

    area, _ = GEOD.geometry_area_perimeter(circle)
    expected = math.pi * radius_m**2
    assert abs(area) == pytest.approx(expected, rel=0.01)


def test_quad_segs_controls_vertex_count() -> None:
    point = Point(10.0, 32.0)
    coarse = GeodesicCircleBuffer(quad_segs=4).buffer(point, 500.0)
    fine = GeodesicCircleBuffer(quad_segs=16).buffer(point, 500.0)
    assert len(fine.exterior.coords) > len(coarse.exterior.coords)


def test_strategies_registry_builds_configured_strategy() -> None:
    cfg = PointBufferConfig(quad_segs=8)
    strategy = STRATEGIES[cfg.strategy](cfg)
    assert strategy.name == "geodesic_circle"
    assert isinstance(strategy, GeodesicCircleBuffer)
    assert strategy.quad_segs == 8


# --- resolve_buffer_radius ---


def test_resolve_buffer_radius_defaults_when_not_requested() -> None:
    cfg = PointBufferConfig()
    assert resolve_buffer_radius(None, cfg) == cfg.default_radius_m


def test_resolve_buffer_radius_rejects_override_when_disabled() -> None:
    cfg = PointBufferConfig(allow_request_override=False)
    with pytest.raises(GeoValidationError) as exc_info:
        resolve_buffer_radius(cfg.default_radius_m, cfg)
    assert exc_info.value.code == ErrorCode.BUFFER_OVERRIDE_NOT_ALLOWED


def test_resolve_buffer_radius_accepts_in_range_override_when_enabled() -> None:
    cfg = PointBufferConfig(allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0)
    assert resolve_buffer_radius(1_000.0, cfg) == 1_000.0


def test_resolve_buffer_radius_rejects_out_of_range_override_when_enabled() -> None:
    cfg = PointBufferConfig(allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0)
    with pytest.raises(GeoValidationError) as exc_info:
        resolve_buffer_radius(10_000.0, cfg)
    assert exc_info.value.code == ErrorCode.BUFFER_RADIUS_OUT_OF_RANGE


def test_resolve_buffer_radius_rejects_below_min_when_enabled() -> None:
    cfg = PointBufferConfig(allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0)
    with pytest.raises(GeoValidationError) as exc_info:
        resolve_buffer_radius(0.1, cfg)
    assert exc_info.value.code == ErrorCode.BUFFER_RADIUS_OUT_OF_RANGE
