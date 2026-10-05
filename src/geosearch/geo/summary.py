"""Compact, LLM-friendly one-line area summaries (~40 tokens).

Pure string formatting — no shapely or pyproj here — so geo/ops.py owns all
the geometry/geodesy math and this module only turns numbers into text.
"""


def _format_coord(value: float, pos_suffix: str, neg_suffix: str) -> str:
    suffix = pos_suffix if value >= 0 else neg_suffix
    return f"{abs(value):.3f} {suffix}"


def _format_range(min_v: float, max_v: float, pos_suffix: str, neg_suffix: str) -> str:
    """e.g. (34.75, 34.80, 'E', 'W') -> '34.750–34.800 E'."""
    if min_v >= 0 and max_v >= 0:
        return f"{min_v:.3f}–{max_v:.3f} {pos_suffix}"
    if min_v < 0 and max_v < 0:
        return f"{abs(max_v):.3f}–{abs(min_v):.3f} {neg_suffix}"
    lo = _format_coord(min_v, pos_suffix, neg_suffix)
    hi = _format_coord(max_v, pos_suffix, neg_suffix)
    return f"{lo}–{hi}"


def format_area_summary(
    area_id: str,
    geom_type: str,
    area_km2: float,
    bbox: tuple[float, float, float, float],
    *,
    buffer_radius_m: float | None = None,
) -> str:
    """buffer_radius_m is only set for an area that came from buffering a Point;
    it switches the shape label from the geometry type to "circle from Point"."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lon_range = _format_range(min_lon, max_lon, "E", "W")
    lat_range = _format_range(min_lat, max_lat, "N", "S")
    if buffer_radius_m is not None:
        shape = f"circle from Point · r={buffer_radius_m:g} m"
    else:
        shape = geom_type
    return f"{area_id} · {shape} · {area_km2:.1f} km² · {lon_range}, {lat_range}"
