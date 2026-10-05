"""Orchestrates request/checks.py, geo/buffer.py, geo/area_store.py and
geo/ops.py in the order fixed by spec section 9. This is the only place that
decides the order; every check itself stays independently testable.
"""

import uuid

from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.geo.area_store import AreaStore
from geosearch.geo.buffer import BufferStrategy, resolve_buffer_radius
from geosearch.geo.ops import AreaOps
from geosearch.request.checks import (
    check_area_within_limit,
    check_coordinate_range,
    check_geometry_is_valid,
    check_no_antimeridian_crossing,
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
from geosearch.request.models import UserRequest, ValidatedRequest


def validate_request(
    request: UserRequest,
    cfg: GeoConfig,
    store: AreaStore,
    ops: AreaOps,
    buffer_strategy: BufferStrategy,
) -> ValidatedRequest:
    notes: list[str] = []

    check_wkt_present(request.wkt)
    check_prompt_present(request.prompt)
    check_prompt_length(request.prompt, cfg.limits.max_prompt_chars)
    check_wkt_size(request.wkt, cfg.limits.max_wkt_bytes)

    geom = parse_wkt(request.wkt)
    check_supported_geometry_type(geom)
    input_geometry_type = geom.geom_type

    geom, z_note = drop_z(geom)
    if z_note:
        notes.append(z_note)

    check_not_empty(geom)
    check_vertex_count(geom, cfg.limits.max_vertices)
    check_coordinate_range(geom)
    check_geometry_is_valid(geom)

    buffer_radius_m: float | None = None
    buffer_strategy_name: str | None = None

    if geom.geom_type == "Point":
        buffer_radius_m = resolve_buffer_radius(request.point_buffer_m, cfg.point_buffer)
        area_geom = buffer_strategy.buffer(geom, buffer_radius_m)
        check_no_antimeridian_crossing(area_geom)  # row 15
        buffer_strategy_name = buffer_strategy.name
        notes.append(f"Point buffered to a {buffer_radius_m:g} m geodesic circle")
    else:
        if request.point_buffer_m is not None:
            raise GeoValidationError(
                ErrorCode.BUFFER_NOT_APPLICABLE,
                "point_buffer_m is only applicable to Point geometries",
                {"geom_type": geom.geom_type},
            )
        area_geom = geom

    check_area_within_limit(area_geom, cfg.limits.max_area_km2)

    area_id = store.put(area_geom)

    return ValidatedRequest(
        request_id=uuid.uuid4().hex,
        area_id=area_id,
        prompt=request.prompt.strip(),
        input_geometry_type=input_geometry_type,
        area_geometry_type=area_geom.geom_type,
        buffer_radius_m=buffer_radius_m,
        buffer_strategy=buffer_strategy_name,
        bbox=ops.bbox(area_id),
        area_km2=ops.area_km2(area_id),
        representative_point=ops.representative_point(area_id),
        area_summary=ops.area_summary(area_id, buffer_radius_m=buffer_radius_m),
        notes=notes,
    )
