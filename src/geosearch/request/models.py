"""Request/response data models. See CLAUDE.md invariant 4: area_id is never client-supplied."""

from typing import Any, Literal

from pydantic import BaseModel

from geosearch.errors import ErrorCode


class UserRequest(BaseModel):
    """What a client sends: geographic context (WKT) plus intent (prompt).

    `wkt` is required for a new conversation but omitted on a follow-up (the area
    is already bound to the conversation). `conversation_id` turns a request into
    a follow-up. `area_id` is never accepted from a client (invariant 4)."""

    wkt: str | None = None
    prompt: str
    point_buffer_m: float | None = None
    conversation_id: str | None = None


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


class UsageSummary(BaseModel):
    """The token cost of one turn, surfaced in the API response. The full
    per-call ledger goes only to the eval report, not here (Stage 2 §11)."""

    model_calls: int
    input_tokens: int
    output_tokens: int
    peak_input_tokens: int
    over_budget: bool
