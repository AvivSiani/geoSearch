"""Exception handlers: every error response is an ErrorEnvelope, status 422."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.request.models import ErrorEnvelope


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GeoValidationError)
    async def handle_geo_validation_error(
        request: Request, exc: GeoValidationError
    ) -> JSONResponse:
        envelope = ErrorEnvelope(code=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(status_code=422, content=envelope.model_dump())

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        envelope = ErrorEnvelope(
            code=_map_pydantic_error(exc),
            message="request validation failed",
            details={"errors": exc.errors()},
        )
        return JSONResponse(status_code=422, content=envelope.model_dump())


def _map_pydantic_error(exc: RequestValidationError) -> ErrorCode:
    """A missing wkt maps to MISSING_WKT, a missing prompt to MISSING_PROMPT,
    anything else (wrong type, malformed JSON body, ...) to INVALID_REQUEST."""
    missing_fields = {error["loc"][-1] for error in exc.errors() if error["type"] == "missing"}
    if "wkt" in missing_fields:
        return ErrorCode.MISSING_WKT
    if "prompt" in missing_fields:
        return ErrorCode.MISSING_PROMPT
    return ErrorCode.INVALID_REQUEST
