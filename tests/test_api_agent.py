"""API tests specific to Stage 2: the agent response, and the new HTTP statuses
(503 model-unavailable, 409 conversation-busy)."""

from typing import Any

import httpx
from conftest import HANDOFF_POLYGON
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from scripted_model import ScriptedChatModel, ai

from geosearch.api.app import create_app
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode


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
    app = create_app(GeoConfig(_env_file=None), model=UnreachableModel(responses=[ai("x")]))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q"})
    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.MODEL_UNAVAILABLE


def test_conversation_busy_returns_409() -> None:
    app = create_app(GeoConfig(_env_file=None), model=ScriptedChatModel(responses=[ai("a")]))
    client = TestClient(app)
    first = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q1"}
    ).json()

    # Hold the conversation's lock to simulate a turn already in flight.
    entry = app.state.runner.registry._entries[first["conversation_id"]]
    entry.lock.acquire()
    try:
        response = client.post(
            "/v1/requests",
            json={"prompt": "q2", "conversation_id": first["conversation_id"]},
        )
        assert response.status_code == 409
        assert response.json()["code"] == ErrorCode.CONVERSATION_BUSY
    finally:
        entry.lock.release()
