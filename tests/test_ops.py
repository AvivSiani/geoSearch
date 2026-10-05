import pytest
import shapely
from conftest import C_SHAPE, HANDOFF_POLYGON, TWO_SQUARES, WITH_HOLE

from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps


@pytest.fixture
def ops() -> AreaOps:
    return AreaOps(InMemoryAreaStore(max_entries=10))


def _put(ops: AreaOps, wkt: str) -> str:
    return ops._store.put(shapely.from_wkt(wkt))


def test_handoff_polygon_area_km2(ops: AreaOps) -> None:
    area_id = _put(ops, HANDOFF_POLYGON)
    assert ops.area_km2(area_id) == pytest.approx(26.2, rel=0.02)


def test_bbox(ops: AreaOps) -> None:
    area_id = _put(ops, HANDOFF_POLYGON)
    assert ops.bbox(area_id) == pytest.approx((34.75, 32.05, 34.80, 32.10))


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (0.5, 0.5, True),  # inside the ring
        (2, 2, False),  # inside the hole
        (0, 2, True),  # on the outer boundary
        (5, 5, False),  # outside entirely
    ],
)
def test_contains_with_hole(ops: AreaOps, lon: float, lat: float, expected: bool) -> None:
    area_id = _put(ops, WITH_HOLE)
    assert ops.contains(area_id, lon, lat) is expected


def test_representative_point_inside_c_shape(ops: AreaOps) -> None:
    area_id = _put(ops, C_SHAPE)
    lon, lat = ops.representative_point(area_id)
    assert ops.contains(area_id, lon, lat)

    geom = shapely.from_wkt(C_SHAPE)
    assert not geom.covers(geom.centroid)  # the centroid is outside; that's why we don't use it


def test_representative_point_inside_two_squares(ops: AreaOps) -> None:
    area_id = _put(ops, TWO_SQUARES)
    lon, lat = ops.representative_point(area_id)
    assert ops.contains(area_id, lon, lat)

    geom = shapely.from_wkt(TWO_SQUARES)
    assert not geom.covers(geom.centroid)


def test_distance_m_is_zero_at_representative_point(ops: AreaOps) -> None:
    area_id = _put(ops, HANDOFF_POLYGON)
    lon, lat = ops.representative_point(area_id)
    assert ops.distance_m(area_id, lon, lat) == pytest.approx(0.0, abs=1e-6)


def test_area_summary_for_polygon(ops: AreaOps) -> None:
    area_id = _put(ops, HANDOFF_POLYGON)
    summary = ops.area_summary(area_id)
    assert summary.startswith(area_id)
    assert "Polygon" in summary
    assert "km²" in summary
    assert "E" in summary and "N" in summary


def test_area_summary_for_point_buffer(ops: AreaOps) -> None:
    area_id = _put(ops, HANDOFF_POLYGON)
    summary = ops.area_summary(area_id, buffer_radius_m=10.0)
    assert "circle from Point" in summary
    assert "r=10" in summary
