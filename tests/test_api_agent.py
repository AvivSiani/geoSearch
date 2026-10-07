"""API tests specific to Stage 2: the agent response, and the new HTTP statuses
(503 model-unavailable, 503 store-unavailable, 409 conversation-busy)."""

from typing import Any

import httpx
from conftest import HANDOFF_POLYGON
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError
from scripted_model import ScriptedChatModel, ai

from geosearch.api.app import create_app
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode
from geosearch.registry.store import InMemoryRegistry


class UnreachableModel(ScriptedChatModel):
    """Mimics a model server that's down."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise httpx.ConnectError("connection refused")


def test_model_unavailable_returns_503() -> None:
    app = create_app(
        GeoConfig(_env_file=None),
        model=UnreachableModel(responses=[ai("x")]),
        tool_registry=InMemoryRegistry(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q"})
    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.MODEL_UNAVAILABLE


def test_conversation_busy_returns_409() -> None:
    app = create_app(
        GeoConfig(_env_file=None),
        model=ScriptedChatModel(responses=[ai("a")]),
        tool_registry=InMemoryRegistry(),
    )
    client = TestClient(app)
    first = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q1"}
    ).json()

    # Hold the conversation's lock to simulate a turn already in flight.
    lock = app.state.runner.registry._lock_for(first["conversation_id"])
    lock.acquire()
    try:
        response = client.post(
            "/v1/requests",
            json={"prompt": "q2", "conversation_id": first["conversation_id"]},
        )
        assert response.status_code == 409
        assert response.json()["code"] == ErrorCode.CONVERSATION_BUSY
    finally:
        lock.release()


def test_response_carries_items_and_answer_source() -> None:
    """Stage 5: the API exposes the grounded items and how the turn finished."""
    from scripted_model import submit

    app = create_app(
        GeoConfig(_env_file=None),
        model=ScriptedChatModel(responses=[submit("No places needed.")]),
        tool_registry=InMemoryRegistry(),
    )
    body = TestClient(app).post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "How big is it?"}
    ).json()
    assert body["answer"] == "No places needed."
    assert body["answer_source"] == "submitted" and body["items"] == []
    assert body["usage"]["summarizer_calls"] == 0


def _app_whose_runner_raises(exc: Exception) -> TestClient:
    app = create_app(
        GeoConfig(_env_file=None),
        model=ScriptedChatModel(responses=[ai("x")]),
        tool_registry=InMemoryRegistry(),
    )

    def handle(_request: Any) -> Any:
        raise exc

    app.state.runner.handle = handle
    return TestClient(app, raise_server_exceptions=False)


def test_store_unavailable_returns_503() -> None:
    client = _app_whose_runner_raises(ServerSelectionTimeoutError("mongodb://u:secret@h"))
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q"})
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == ErrorCode.STORE_UNAVAILABLE
    assert "secret" not in response.text  # the driver's message may carry the URI


def test_other_store_errors_are_not_an_outage() -> None:
    # A DuplicateKeyError is a bug in our code, not MongoDB being down.
    client = _app_whose_runner_raises(DuplicateKeyError("dup"))
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q"})
    assert response.status_code == 500
