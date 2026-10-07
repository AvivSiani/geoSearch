"""POST /v1/requests — the agent runs behind the same endpoint as Stage 1, so
clients that only need an answer never change. GET /v1/conversations/{id}
returns a conversation's recorded turns (Stage 6). The route is a thin translation
layer: it hands the request to the RequestRunner and maps model-connectivity
failures (model server, MongoDB) to typed 503s."""

import httpx
from fastapi import APIRouter, Request
from pymongo.errors import ConnectionFailure

from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.request.models import (
    AgentResponse,
    ConversationHistory,
    ConversationTurn,
    ErrorEnvelope,
    UserRequest,
)

router = APIRouter()

# Connectivity failures that mean "the model server isn't reachable" rather than
# a bug. GeoValidationError is not among these, so typed validation/conversation
# errors propagate untouched to their own handler.
_MODEL_DOWN_ERRORS = (
    ConnectionError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.TimeoutException,
)


def _store_unavailable(exc: ConnectionFailure) -> GeoValidationError:
    """MongoDB went away mid-request. pymongo's ConnectionFailure (which covers
    server-selection and network timeouts) is not a builtin ConnectionError, so
    it never lands in _MODEL_DOWN_ERRORS. Any other PyMongoError is a bug, not
    an outage, and stays a 500. Never a fallback to memory."""
    return GeoValidationError(
        ErrorCode.STORE_UNAVAILABLE,
        "the conversation store is unreachable",
        {"error": type(exc).__name__},  # the message may echo the URI
    )


@router.post(
    "/v1/requests",
    response_model=AgentResponse,
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def create_request(request: UserRequest, http_request: Request) -> AgentResponse:
    """Sync def: the agent invoke is driven synchronously; FastAPI runs it in a
    worker thread."""
    runner = http_request.app.state.runner
    try:  # the runner refreshes the catalog and, if it changed, rebuilds the agent
        outcome = runner.handle(request)
    except _MODEL_DOWN_ERRORS as exc:
        raise GeoValidationError(
            ErrorCode.MODEL_UNAVAILABLE,
            "the model server is unreachable",
            {"error": str(exc)},
        ) from exc
    except ConnectionFailure as exc:
        raise _store_unavailable(exc) from exc

    return AgentResponse(
        conversation_id=outcome.conversation_id,
        turn=outcome.turn,
        request_id=outcome.request_id,
        area_summary=outcome.area_summary,
        answer=outcome.answer,
        stopped_reason=outcome.stopped_reason,
        usage=outcome.usage,
        items=outcome.items,
        answer_source=outcome.answer_source,
    )


@router.get(
    "/v1/conversations/{conversation_id}",
    response_model=ConversationHistory,
    responses={404: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}},
)
def get_conversation(conversation_id: str, http_request: Request) -> ConversationHistory:
    """The turn history from the conversation record (Stage 6). An unknown or
    expired id is a 404, exactly as for a follow-up."""
    try:
        record = http_request.app.state.runner.registry.live(conversation_id)
    except ConnectionFailure as exc:
        raise _store_unavailable(exc) from exc
    return ConversationHistory(
        conversation_id=record.conversation_id,
        user_id=record.user_id,
        area_id=record.area_id,
        area_summary=record.area_summary,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
        turns=[ConversationTurn(**turn) for turn in record.turns],
    )
