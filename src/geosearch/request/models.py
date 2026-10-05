"""Request/response data models. See CLAUDE.md invariant 4: area_id is never client-supplied."""

from typing import Any, Literal

from pydantic import BaseModel

from geosearch.errors import ErrorCode


class UserRequest(BaseModel):
    """What a client sends: geographic context (WKT) plus intent (prompt)."""

    wkt: str
    prompt: str
    point_buffer_m: float | None = None


class ValidatedRequest(BaseModel):
    """What validate_request() returns: a deterministic, LLM-ready summary of the request."""

    request_id: str
    area_id: str
    prompt: str
    input_geometry_type: Literal["Polygon", "MultiPolygon", "Point"]
    area_geometry_type: Literal["Polygon", "MultiPolygon"]
    buffer_radius_m: float | None
    buffer_strategy: str | None
    bbox: tuple[float, float, float, float]
    area_km2: float
    representative_point: tuple[float, float]
    area_summary: str
    notes: list[str]


class ErrorEnvelope(BaseModel):
    """What every error response looks like, regardless of which check failed."""

    code: ErrorCode
    message: str
    details: dict[str, Any] = {}
