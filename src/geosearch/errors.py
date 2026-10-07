"""Error vocabulary shared by request validation and the API layer."""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """All error codes the core can raise. See CLAUDE.md invariant 1: never repair geometry."""

    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_WKT = "MISSING_WKT"
    MISSING_PROMPT = "MISSING_PROMPT"
    PROMPT_TOO_LONG = "PROMPT_TOO_LONG"
    WKT_TOO_LARGE = "WKT_TOO_LARGE"
    WKT_PARSE_ERROR = "WKT_PARSE_ERROR"
    UNSUPPORTED_GEOMETRY = "UNSUPPORTED_GEOMETRY"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    BUFFER_NOT_APPLICABLE = "BUFFER_NOT_APPLICABLE"
    BUFFER_OVERRIDE_NOT_ALLOWED = "BUFFER_OVERRIDE_NOT_ALLOWED"
    BUFFER_RADIUS_OUT_OF_RANGE = "BUFFER_RADIUS_OUT_OF_RANGE"
    AREA_TOO_LARGE = "AREA_TOO_LARGE"

    # Stage 2: agent and conversation errors.
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    AREA_MISMATCH = "AREA_MISMATCH"
    CONVERSATION_BUSY = "CONVERSATION_BUSY"
    CONVERSATION_LIMIT = "CONVERSATION_LIMIT"

    # Stage 6: the conversation store (MongoDB) is unreachable.
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"


class GeoValidationError(Exception):
    """The single exception type the core raises. api/ maps this to an ErrorEnvelope."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
