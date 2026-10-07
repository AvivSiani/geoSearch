"""Stage 6 step 6: resume checks (tools that left the registry are unloaded,
with a note to the model) and GET /v1/conversations/{id}."""

from typing import Any

import pytest
from conftest import HANDOFF_POLYGON
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage
from persistence_helpers import mongo_cfg
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError
from scripted_model import ScriptedChatModel, submit
from test_disclosure_flow import _first, _load, _next, _runner

from geosearch.agent.state import merge_loaded_tools
from geosearch.api.app import create_app
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode
from geosearch.registry.store import InMemoryRegistry

NOTICE = "Tool source_9001 is no longer available and was unloaded."


def _system(call: list) -> str:
    return next(str(m.content) for m in call if isinstance(m, SystemMessage))


def test_reducer_drops_only_on_an_explicit_drop() -> None:
    assert merge_loaded_tools([17, 9001, 101], {"drop": [9001, 5]}) == [17, 101]
    assert merge_loaded_tools(None, {"drop": [9001]}) == []
    assert merge_loaded_tools([17], [9001]) == [17, 9001]  # loads still union


def test_a_tool_that_left_the_registry_is_unloaded_with_a_notice() -> None:
    runner, model, registry = _runner(
        [_load(9001, 101), submit("Loaded."), submit("Second.", call_id="s2"),
         submit("Third.", call_id="s3")],
    )  # fmt: skip
    first = _first(runner)
    assert first.state["loaded_tools"] == [9001, 101]
    turn1_calls = len(model.calls)

    registry.delete_tool(9001)  # the next request rebuilds the agent
    second = _next(runner, first)
    assert second.state["loaded_tools"] == [101]
    assert second.state["notices"] == [NOTICE]
    system = _system(model.calls[turn1_calls])
    assert system.endswith(NOTICE)
    assert "9001:" not in system  # and the catalog no longer lists it

    third = _next(runner, first, "q3")  # a notice is for one turn only
    assert third.state["notices"] == [] and third.state["loaded_tools"] == [101]
    assert "no longer available" not in _system(model.calls[-1])


def test_prompt_is_unchanged_when_nothing_left() -> None:
    runner, model, _ = _runner([_load(9001), submit("Loaded."), submit("Second.", call_id="s2")])
    first = _first(runner)
    before = _system(model.calls[-1])
    second = _next(runner, first)
    assert second.state["notices"] == []
    assert _system(model.calls[-1]) == before  # byte-identical prompt


# --- GET /v1/conversations/{id} ----------------------------------------------


def _client(cfg: GeoConfig) -> TestClient:
    model = ScriptedChatModel(
        responses=[submit("About 26 km² [i9].", [], "s1"), submit("Still 26.", [], "s2")]
    )
    app = create_app(cfg, model=model, tool_registry=InMemoryRegistry())
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(params=["memory", "mongodb"])
def client(request: pytest.FixtureRequest) -> TestClient:
    if request.param == "memory":
        return _client(GeoConfig(_env_file=None))
    return _client(mongo_cfg(request.getfixturevalue("mongo_db")))


def test_history_lists_the_turns(client: TestClient) -> None:
    first = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "How big?"})
    conversation_id = first.json()["conversation_id"]
    client.post("/v1/requests", json={"prompt": "Sure?", "conversation_id": conversation_id})

    response = client.get(f"/v1/conversations/{conversation_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert "user_id" in body and body["user_id"] is None
    assert body["area_id"] == first.json()["area_summary"].split(" ")[0]
    assert [(t["turn"], t["prompt"], t["answer"]) for t in body["turns"]] == [
        (1, "How big?", "About 26 km²."),  # an invalid citation is stripped, as in the answer
        (2, "Sure?", "Still 26."),
    ]
    turn = body["turns"][0]
    assert turn["tokens"]["model_calls"] == 1 and turn["recovered"] is False
    assert turn["request_id"] == first.json()["request_id"]
    # Never the WKT: the summary's bbox is fine, the polygon's vertices are not.
    assert "POLYGON" not in response.text and "34.75 32.05" not in response.text


def test_history_of_an_unknown_conversation_is_404(client: TestClient) -> None:
    response = client.get("/v1/conversations/nope")
    assert response.status_code == 404
    assert response.json()["code"] == ErrorCode.CONVERSATION_NOT_FOUND


def test_history_when_the_store_is_down_is_503() -> None:
    client = _client(GeoConfig(_env_file=None))
    registry = client.app.state.runner.registry

    def down(_conversation_id: str) -> Any:
        raise ServerSelectionTimeoutError("down")

    registry.live = down
    response = client.get("/v1/conversations/abc")
    assert response.status_code == 503
    assert response.json()["code"] == ErrorCode.STORE_UNAVAILABLE


def test_history_in_mongodb_is_the_stored_document(mongo_db: Database) -> None:
    client = _client(mongo_cfg(mongo_db))
    first = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q"}).json()
    doc = mongo_db["conversations"].find_one({"_id": first["conversation_id"]})
    body = client.get(f"/v1/conversations/{first['conversation_id']}").json()
    assert body["turns"][0]["answer"] == doc["turns"][0]["answer"]
    assert body["area_id"] == doc["area_id"]
