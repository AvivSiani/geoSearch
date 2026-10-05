import pytest
import shapely
from conftest import (
    BOWTIE,
    BROKEN,
    EMPTY,
    HANDOFF_POLYGON,
    LINE,
    OUT_OF_RANGE,
    POINT_Z,
    TLV_POINT,
)

from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.request.checks import (
    check_coordinate_range,
    check_geometry_is_valid,
    check_not_empty,
    check_prompt_length,
    check_prompt_present,
    check_supported_geometry_type,
    check_vertex_count,
    check_wkt_present,
    check_wkt_size,
    drop_z,
    parse_wkt,
)

# --- row 2: MISSING_WKT ---


@pytest.mark.parametrize("wkt", ["", "   ", "\t\n"])
def test_check_wkt_present_rejects_blank(wkt: str) -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_wkt_present(wkt)
    assert exc_info.value.code == ErrorCode.MISSING_WKT


def test_check_wkt_present_accepts_nonblank() -> None:
    check_wkt_present(HANDOFF_POLYGON)


# --- row 3: MISSING_PROMPT ---


@pytest.mark.parametrize("prompt", ["", "   "])
def test_check_prompt_present_rejects_blank(prompt: str) -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_prompt_present(prompt)
    assert exc_info.value.code == ErrorCode.MISSING_PROMPT


def test_check_prompt_present_accepts_nonblank() -> None:
    check_prompt_present("find food")


# --- row 4: PROMPT_TOO_LONG ---


def test_check_prompt_length_rejects_too_long() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_prompt_length("x" * 10, max_chars=5)
    assert exc_info.value.code == ErrorCode.PROMPT_TOO_LONG
    assert exc_info.value.details["length"] == 10


def test_check_prompt_length_accepts_within_limit() -> None:
    check_prompt_length("x" * 5, max_chars=5)


# --- row 5: WKT_TOO_LARGE (byte size) ---


def test_check_wkt_size_rejects_too_large() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_wkt_size("x" * 10, max_bytes=5)
    assert exc_info.value.code == ErrorCode.WKT_TOO_LARGE


def test_check_wkt_size_accepts_within_limit() -> None:
    check_wkt_size(HANDOFF_POLYGON, max_bytes=1_000)


# --- row 6: WKT_PARSE_ERROR ---


def test_parse_wkt_rejects_broken_wkt() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        parse_wkt(BROKEN)
    assert exc_info.value.code == ErrorCode.WKT_PARSE_ERROR


def test_parse_wkt_accepts_valid_wkt() -> None:
    geom = parse_wkt(HANDOFF_POLYGON)
    assert geom.geom_type == "Polygon"


# --- row 7: UNSUPPORTED_GEOMETRY ---


def test_check_supported_geometry_type_rejects_linestring() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_supported_geometry_type(parse_wkt(LINE))
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_GEOMETRY


@pytest.mark.parametrize("wkt", [HANDOFF_POLYGON, TLV_POINT])
def test_check_supported_geometry_type_accepts_polygon_and_point(wkt: str) -> None:
    check_supported_geometry_type(parse_wkt(wkt))


# --- row 8: Z drop (not a rejection) ---


def test_drop_z_drops_z_and_notes_it() -> None:
    geom, note = drop_z(parse_wkt(POINT_Z))
    assert not geom.has_z
    assert note == "Z coordinates dropped"


def test_drop_z_is_noop_without_z() -> None:
    original = parse_wkt(TLV_POINT)
    geom, note = drop_z(original)
    assert geom.equals(original)
    assert note is None


# --- row 9: INVALID_GEOMETRY (empty) ---


def test_check_not_empty_rejects_empty() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_not_empty(parse_wkt(EMPTY))
    assert exc_info.value.code == ErrorCode.INVALID_GEOMETRY


def test_check_not_empty_accepts_nonempty() -> None:
    check_not_empty(parse_wkt(HANDOFF_POLYGON))


# --- row 10: WKT_TOO_LARGE (vertex count) ---


def test_check_vertex_count_rejects_too_many_vertices() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_vertex_count(parse_wkt(HANDOFF_POLYGON), max_vertices=3)
    assert exc_info.value.code == ErrorCode.WKT_TOO_LARGE
    assert exc_info.value.details["vertex_count"] == 5


def test_check_vertex_count_accepts_within_limit() -> None:
    check_vertex_count(parse_wkt(HANDOFF_POLYGON), max_vertices=100)


# --- row 11 / 15: OUT_OF_RANGE ---


def test_check_coordinate_range_rejects_out_of_range() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_coordinate_range(parse_wkt(OUT_OF_RANGE))
    assert exc_info.value.code == ErrorCode.OUT_OF_RANGE


def test_check_coordinate_range_accepts_in_range() -> None:
    check_coordinate_range(parse_wkt(HANDOFF_POLYGON))


# --- row 12: INVALID_GEOMETRY (is_valid) ---


def test_check_geometry_is_valid_rejects_bowtie() -> None:
    with pytest.raises(GeoValidationError) as exc_info:
        check_geometry_is_valid(parse_wkt(BOWTIE))
    assert exc_info.value.code == ErrorCode.INVALID_GEOMETRY
    assert "reason" in exc_info.value.details


def test_check_geometry_is_valid_accepts_valid_geometry() -> None:
    check_geometry_is_valid(parse_wkt(HANDOFF_POLYGON))


def test_check_geometry_is_valid_never_repairs() -> None:
    bowtie = parse_wkt(BOWTIE)
    with pytest.raises(GeoValidationError):
        check_geometry_is_valid(bowtie)
    # the geometry object itself must be untouched
    assert shapely.to_wkt(bowtie, trim=True) == shapely.to_wkt(parse_wkt(BOWTIE), trim=True)
