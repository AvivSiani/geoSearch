import pytest
from pydantic import ValidationError

from geosearch.config import GeoConfig, LimitsConfig, PointBufferConfig


def test_defaults(cfg: GeoConfig) -> None:
    assert cfg.point_buffer.default_radius_m == 10.0
    assert cfg.point_buffer.min_radius_m == 1.0
    assert cfg.point_buffer.max_radius_m == 5_000.0
    assert cfg.point_buffer.strategy == "geodesic_circle"
    assert cfg.point_buffer.allow_request_override is False
    assert cfg.limits.max_area_km2 == 100.0
    assert cfg.area_store.max_entries == 10_000


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M", "1000")
    cfg = GeoConfig(_env_file=None)
    assert cfg.point_buffer.default_radius_m == 1000.0


def test_invalid_radius_bounds_fails_fast() -> None:
    with pytest.raises(ValidationError, match="min_radius_m"):
        PointBufferConfig(min_radius_m=50.0, default_radius_m=10.0)


def test_invalid_quad_segs_fails_fast() -> None:
    with pytest.raises(ValidationError, match="quad_segs"):
        PointBufferConfig(quad_segs=2)


def test_invalid_limit_fails_fast() -> None:
    with pytest.raises(ValidationError, match="max_area_km2"):
        LimitsConfig(max_area_km2=0)
