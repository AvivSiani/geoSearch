"""POST /v1/requests — Stage 1's only endpoint. In Stage 2 the agent runs
behind this same endpoint, so clients never need to change."""

from fastapi import APIRouter, Request

from geosearch.request.models import ErrorEnvelope, UserRequest, ValidatedRequest
from geosearch.request.validate import validate_request

router = APIRouter()


@router.post(
    "/v1/requests",
    response_model=ValidatedRequest,
    responses={422: {"model": ErrorEnvelope}},
)
def create_request(request: UserRequest, http_request: Request) -> ValidatedRequest:
    """Sync def: validation is CPU-bound shapely/pyproj work, not I/O."""
    state = http_request.app.state
    return validate_request(request, state.cfg, state.store, state.ops, state.buffer_strategy)
