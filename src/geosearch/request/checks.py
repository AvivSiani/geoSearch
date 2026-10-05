"""Validation checks from spec section 9, rows 2-12 and 16.

Each check is a small, independently testable function that takes primitive
arguments (not a GeoConfig) and raises GeoValidationError on failure.
request/validate.py is the only place these are composed, in order.
"""

import shapely
from pyproj import Geod
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry

from geosearch.errors import ErrorCode, GeoValidationError

SUPPORTED_GEOMETRY_TYPES = {"Polygon", "MultiPolygon", "Point"}
_GEOD = Geod(ellps="WGS84")


def check_wkt_present(wkt: str) -> None:
    """Row 2: wkt absent, empty or whitespace. True absence is a pydantic error;
    this catches the empty-string case that reaches us as a valid str."""
    if not wkt or not wkt.strip():
        raise GeoValidationError(ErrorCode.MISSING_WKT, "wkt is required and must not be blank")


def check_prompt_present(prompt: str) -> None:
    """Row 3: prompt absent, empty or whitespace."""
    if not prompt or not prompt.strip():
        raise GeoValidationError(
            ErrorCode.MISSING_PROMPT, "prompt is required and must not be blank"
        )


def check_prompt_length(prompt: str, max_chars: int) -> None:
    """Row 4: prompt longer than the configured limit."""
    if len(prompt) > max_chars:
        raise GeoValidationError(
            ErrorCode.PROMPT_TOO_LONG,
            f"prompt exceeds the {max_chars} character limit",
            {"length": len(prompt), "max_chars": max_chars},
        )


def check_wkt_size(wkt: str, max_bytes: int) -> None:
    """Row 5: reject an oversized wkt by its UTF-8 byte size, before parsing it."""
    size = len(wkt.encode("utf-8"))
    if size > max_bytes:
        raise GeoValidationError(
            ErrorCode.WKT_TOO_LARGE,
            f"wkt exceeds the {max_bytes} byte limit",
            {"size_bytes": size, "max_bytes": max_bytes},
        )


def parse_wkt(wkt: str) -> BaseGeometry:
    """Row 6: shapely cannot parse it. Shapely raises GEOSException for malformed WKT."""
    try:
        return shapely.from_wkt(wkt)
    except GEOSException as exc:
        raise GeoValidationError(ErrorCode.WKT_PARSE_ERROR, f"could not parse wkt: {exc}") from exc


def check_supported_geometry_type(geom: BaseGeometry) -> None:
    """Row 7: only Polygon, MultiPolygon and Point are supported."""
    if geom.geom_type not in SUPPORTED_GEOMETRY_TYPES:
        raise GeoValidationError(
            ErrorCode.UNSUPPORTED_GEOMETRY,
            f"geometry type {geom.geom_type} is not supported; "
            "expected Polygon, MultiPolygon or Point",
            {"geom_type": geom.geom_type},
        )


def drop_z(geom: BaseGeometry) -> tuple[BaseGeometry, str | None]:
    """Row 8: not a rejection. Every later geo operation is 2D-only, so a Z
    coordinate is dropped rather than rejected, with the change reported back
    via a note so nothing is silently different from what the caller sent."""
    if not geom.has_z:
        return geom, None
    return shapely.force_2d(geom), "Z coordinates dropped"


def check_not_empty(geom: BaseGeometry) -> None:
    """Row 9: an empty geometry has no area and nothing to validate further."""
    if geom.is_empty:
        raise GeoValidationError(ErrorCode.INVALID_GEOMETRY, "geometry is empty")


def check_vertex_count(geom: BaseGeometry, max_vertices: int) -> None:
    """Row 10: reuses WKT_TOO_LARGE, now measuring shape complexity instead of byte size."""
    count = shapely.get_num_coordinates(geom)
    if count > max_vertices:
        raise GeoValidationError(
            ErrorCode.WKT_TOO_LARGE,
            f"geometry has {count} vertices, exceeding the limit of {max_vertices}",
            {"vertex_count": count, "max_vertices": max_vertices},
        )


def check_coordinate_range(geom: BaseGeometry) -> None:
    """Row 11: valid WGS84 ranges.
    Must run after check_not_empty — an empty geometry's bounds are all NaN."""
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    if min_lon < -180 or max_lon > 180 or min_lat < -90 or max_lat > 90:
        raise GeoValidationError(
            ErrorCode.OUT_OF_RANGE,
            "geometry coordinates must be within lon [-180, 180] and lat [-90, 90]",
            {"bounds": (min_lon, min_lat, max_lon, max_lat)},
        )


def check_no_antimeridian_crossing(geom: BaseGeometry) -> None:
    """Row 15: a buffered circle near +/-180 longitude can cross the
    antimeridian, which is out of scope.

    A plain bbox check (check_coordinate_range) can't see this: pyproj's
    inverse projection normalizes every output longitude back into
    [-180, 180], so a wrapped circle's vertices each individually look
    in-range — the tell is that *consecutive* vertices jump from ~180 to
    ~-180 instead of varying smoothly. Detect that jump directly.
    """
    coords = list(geom.exterior.coords)
    for (lon1, _), (lon2, _) in zip(coords, coords[1:], strict=False):
        if abs(lon2 - lon1) > 180:
            raise GeoValidationError(
                ErrorCode.OUT_OF_RANGE,
                "buffered circle crosses the antimeridian, which is out of scope",
            )


def check_geometry_is_valid(geom: BaseGeometry) -> None:
    """Row 12: never repair invalid geometry (invariant 1) — only report why it's invalid."""
    if not geom.is_valid:
        raise GeoValidationError(
            ErrorCode.INVALID_GEOMETRY,
            "geometry is not valid",
            {"reason": shapely.is_valid_reason(geom)},
        )


def check_area_within_limit(geom: BaseGeometry, max_area_km2: float) -> None:
    """Row 16: applies to whichever geometry is about to be stored — the
    buffered circle for a Point, or the input geometry otherwise."""
    area, _ = _GEOD.geometry_area_perimeter(geom)
    area_km2 = abs(area) / 1e6
    if area_km2 > max_area_km2:
        raise GeoValidationError(
            ErrorCode.AREA_TOO_LARGE,
            f"area is {area_km2:.1f} km², exceeding the limit of {max_area_km2} km²",
            {"area_km2": area_km2, "max_area_km2": max_area_km2},
        )
