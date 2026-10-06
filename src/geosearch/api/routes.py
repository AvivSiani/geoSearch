"""POST /v1/requests — the agent runs behind the same endpoint as Stage 1, so
clients that only need an answer never change. The route is a thin translation
layer: it hands the request to the RequestRunner and maps model-connectivity
failures to a typed 503."""

import httpx
from fastapi import APIRouter, Request

from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.request.models import AgentResponse, ErrorEnvelope, UserRequest

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
