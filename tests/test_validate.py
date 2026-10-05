import pytest
from conftest import BOWTIE, HANDOFF_POLYGON, HUGE, POINT_Z, TLV_POINT

from geosearch.config import GeoConfig, PointBufferConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.geo.area_store import AreaStore
from geosearch.geo.buffer import BufferStrategy
from geosearch.geo.ops import AreaOps
from geosearch.request.models import UserRequest
from geosearch.request.validate import validate_request


def test_handoff_polygon_succeeds(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest(wkt=HANDOFF_POLYGON, prompt="find a good restaurant")
    result = validate_request(request, cfg, store, ops, buffer_strategy)

    assert result.input_geometry_type == "Polygon"
    assert result.area_geometry_type == "Polygon"
    assert result.buffer_radius_m is None
    assert result.buffer_strategy is None
    assert result.area_km2 == pytest.approx(26.2, rel=0.02)
    assert result.notes == []
    assert result.area_id.startswith("area_")


def test_point_is_buffered_with_default_radius(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest(wkt=TLV_POINT, prompt="find a good restaurant")
    result = validate_request(request, cfg, store, ops, buffer_strategy)

    assert result.input_geometry_type == "Point"
    assert result.area_geometry_type == "Polygon"
    assert result.buffer_radius_m == cfg.point_buffer.default_radius_m == 10.0
    assert result.buffer_strategy == "geodesic_circle"
    assert any("buffered" in note for note in result.notes)


def test_point_z_drops_z_and_still_buffers(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest(wkt=POINT_Z, prompt="find a good restaurant")
    result = validate_request(request, cfg, store, ops, buffer_strategy)

    assert "Z coordinates dropped" in result.notes
    assert result.buffer_radius_m == cfg.point_buffer.default_radius_m


def test_buffer_override_disabled_rejects_requested_radius(
    store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    cfg = GeoConfig(_env_file=None, point_buffer=PointBufferConfig(allow_request_override=False))
    request = UserRequest(wkt=TLV_POINT, prompt="x", point_buffer_m=100.0)
    with pytest.raises(GeoValidationError) as exc_info:
        validate_request(request, cfg, store, ops, buffer_strategy)
    assert exc_info.value.code == ErrorCode.BUFFER_OVERRIDE_NOT_ALLOWED


def test_buffer_override_enabled_in_range_is_used(
    store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    cfg = GeoConfig(
        _env_file=None,
        point_buffer=PointBufferConfig(
            allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0
        ),
    )
    request = UserRequest(wkt=TLV_POINT, prompt="x", point_buffer_m=250.0)
    result = validate_request(request, cfg, store, ops, buffer_strategy)
    assert result.buffer_radius_m == 250.0


def test_buffer_override_enabled_out_of_range_rejected(
    store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    cfg = GeoConfig(
        _env_file=None,
        point_buffer=PointBufferConfig(
            allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0
        ),
    )
    request = UserRequest(wkt=TLV_POINT, prompt="x", point_buffer_m=50_000.0)
    with pytest.raises(GeoValidationError) as exc_info:
        validate_request(request, cfg, store, ops, buffer_strategy)
    assert exc_info.value.code == ErrorCode.BUFFER_RADIUS_OUT_OF_RANGE


def test_buffer_field_with_polygon_is_not_applicable(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest(wkt=HANDOFF_POLYGON, prompt="x", point_buffer_m=100.0)
    with pytest.raises(GeoValidationError) as exc_info:
        validate_request(request, cfg, store, ops, buffer_strategy)
    assert exc_info.value.code == ErrorCode.BUFFER_NOT_APPLICABLE


def test_buffered_circle_crossing_antimeridian_is_out_of_range(
    store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    cfg = GeoConfig(
        _env_file=None,
        point_buffer=PointBufferConfig(
            allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0
        ),
    )
    request = UserRequest(
        wkt="POINT(179.999 0)", prompt="x", point_buffer_m=5_000.0
    )
    with pytest.raises(GeoValidationError) as exc_info:
        validate_request(request, cfg, store, ops, buffer_strategy)
    assert exc_info.value.code == ErrorCode.OUT_OF_RANGE


def test_area_too_large_is_rejected(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest(wkt=HUGE, prompt="x")
    with pytest.raises(GeoValidationError) as exc_info:
        validate_request(request, cfg, store, ops, buffer_strategy)
    assert exc_info.value.code == ErrorCode.AREA_TOO_LARGE


def test_different_configured_radius_gives_different_area_id(
    store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    cfg_10m = GeoConfig(_env_file=None, point_buffer=PointBufferConfig(default_radius_m=10.0))
    cfg_20m = GeoConfig(_env_file=None, point_buffer=PointBufferConfig(default_radius_m=20.0))
    request = UserRequest(wkt=TLV_POINT, prompt="x")

    result_10m = validate_request(request, cfg_10m, store, ops, buffer_strategy)
    result_20m = validate_request(request, cfg_20m, store, ops, buffer_strategy)

    assert result_10m.area_id != result_20m.area_id


def test_client_cannot_supply_area_id(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest.model_validate(
        {"wkt": HANDOFF_POLYGON, "prompt": "x", "area_id": "area_hacked"}
    )
    result = validate_request(request, cfg, store, ops, buffer_strategy)
    assert result.area_id != "area_hacked"
    assert result.area_id.startswith("area_")


def test_invalid_geometry_stores_nothing(
    cfg: GeoConfig, store: AreaStore, ops: AreaOps, buffer_strategy: BufferStrategy
) -> None:
    request = UserRequest(wkt=BOWTIE, prompt="x")
    with pytest.raises(GeoValidationError) as exc_info:
        validate_request(request, cfg, store, ops, buffer_strategy)
    assert exc_info.value.code == ErrorCode.INVALID_GEOMETRY
    assert len(store._areas) == 0
