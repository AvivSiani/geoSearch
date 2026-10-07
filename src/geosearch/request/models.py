"""Request/response data models. See CLAUDE.md invariant 4: area_id is never client-supplied."""

from datetime import datetime
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
    summarizer_calls: int = 0  # Stage 5: not counted in model_calls/input_tokens above
    summarizer_input_tokens: int = 0


class ResponseItem(BaseModel):
    """One grounded item in an answer (Stage 5), built from what a tool returned
    — never from model text. `data` holds the tool's other fields."""

    id: str
    source_id: int
    name: str | None = None
    lon: float | None = None
    lat: float | None = None
    data: dict[str, Any] = {}


class AgentResponse(BaseModel):
    """What POST /v1/requests returns in Stage 2: the agent's answer plus the
    conversation handle and the turn's token cost. Carries no geometry."""

    conversation_id: str
    turn: int
    request_id: str
    area_summary: str
    answer: str
    stopped_reason: Literal["finished", "call_limit"]
    usage: UsageSummary
    items: list[ResponseItem] = []
    answer_source: Literal["submitted", "fallback"] = "fallback"


class TurnTokens(BaseModel):
    model_calls: int
    input_tokens: int
    output_tokens: int
    summarizer_calls: int = 0
    summarizer_input_tokens: int = 0


class ConversationTurn(BaseModel):
    """One finished turn as recorded (Stage 6). `recovered` marks a turn rebuilt
    from its checkpoint after its record write was lost; its tokens and start
    time are then unknown."""

    turn: int
    request_id: str
    prompt: str
    answer: str
    item_ids: list[str]
    answer_source: Literal["submitted", "fallback"]
    stopped_reason: Literal["finished", "call_limit"]
    tokens: TurnTokens | None
    started_at: datetime | None
    finished_at: datetime
    recovered: bool = False


class ConversationHistory(BaseModel):
    """GET /v1/conversations/{id}: the readable history, never the agent state.
    No WKT, provider ids or raw rows. `user_id` is a placeholder for future
    per-user ownership and is always null for now."""

    conversation_id: str
    user_id: None
    area_id: str
    area_summary: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    turns: list[ConversationTurn]
