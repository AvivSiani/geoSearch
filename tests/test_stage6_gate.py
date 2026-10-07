"""Stage 6 gate, scripted, on real MongoDB: a conversation continues after a
simulated restart.

App 1 runs the places turn (loads 9101/9102, finds items) and is shut down,
closing its MongoDB clients. App 2 is a brand-new app on the same database: a
fresh model, agent, saver instance, conversation registry and an empty area
LRU. It resumes by conversation_id alone and must see the same area, the same
loaded tools and items, and turn 1 as context. The tool registry lives in the
same MongoDB, as in production.
"""

import yaml
from conftest import HANDOFF_POLYGON
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from persistence_helpers import mongo_cfg
from pymongo.database import Database
from scripted_model import ScriptedChatModel, ai, submit, tool_call

from geosearch.api.app import create_app
from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import MongoRegistry

EN = "Find me a good Asian restaurant that is not too expensive."


def _seed_places(db: Database) -> MongoRegistry:
    registry = MongoRegistry(db)
    with open("registry/seeds/places.yaml") as f:
        for tool in yaml.safe_load(f)["tools"]:
            registry.upsert_tool(ToolDefinition(**tool))
    return registry


def _app(db: Database, model: ScriptedChatModel) -> FastAPI:
    # One scripted model plays the main agent and the summarizer, in call order.
    return create_app(mongo_cfg(db), model=model, tool_registry=MongoRegistry(db))


def test_conversation_continues_after_restart(mongo_db: Database) -> None:
    _seed_places(mongo_db)
    model_1 = ScriptedChatModel(
        responses=[
            ai("", tool_calls=[tool_call("load_tools", {"source_ids": [9101, 9102]}, "l1")]),
            ai("", tool_calls=[tool_call(
                "source_9101", {"query": "asian restaurant", "max_price": "moderate"}, "s1")]),
            ai("Lotus Noodle Bar [i1] (4.6), Saigon Corner [i3] (4.5, cheap)."),  # summarizer
            submit("Try Lotus Noodle Bar [i1] or Saigon Corner [i3].", ["i1", "i3"]),
        ]
    )  # fmt: skip
    app_1 = _app(mongo_db, model_1)
    first = TestClient(app_1).post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": EN})
    assert first.status_code == 200, first.text
    first = first.json()
    assert [i["id"] for i in first["items"]] == ["i1", "i3"]
    app_1.state.persistence.close()  # the old process is gone

    # --- restart -------------------------------------------------------------
    model_2 = ScriptedChatModel(
        responses=[
            # 9102 is used without loading it again: it must still be loaded.
            ai("", tool_calls=[tool_call("source_9102", {"item_id": "i3"}, "d2")]),
            ai("[i3] Saigon Corner: open daily, phone listed."),  # summarizer
            submit("Saigon Corner [i3] is open daily.", ["i3"], "sub2"),
        ]
    )
    app_2 = _app(mongo_db, model_2)
    area_lru = app_2.state.persistence.area_store._cache
    assert len(area_lru._areas) == 0  # nothing carried over in memory
    client = TestClient(app_2)
    follow_up = {
        "prompt": "Is the second one open daily?",
        "conversation_id": first["conversation_id"],
    }
    second = client.post("/v1/requests", json=follow_up)
    assert second.status_code == 200, second.text
    second = second.json()

    # Same conversation, same area, the next turn.
    assert second["conversation_id"] == first["conversation_id"]
    assert second["turn"] == 2
    assert second["area_summary"] == first["area_summary"]
    area_id = first["area_summary"].split(" ")[0]
    assert area_id in area_lru  # read through from the `areas` collection

    # Same loaded tools and items: the details call ran on turn 1's i3.
    assert second["answer"] == "Saigon Corner [i3] is open daily."
    assert [i["id"] for i in second["items"]] == ["i3"]
    assert second["items"][0]["name"] == "Saigon Corner"
    assert second["items"][0]["data"].get("phone")  # details merged into the item

    # Earlier turns used as context: app 2's first model call carries turn 1.
    first_call = model_2.calls[0]
    system = next(str(m.content) for m in first_call if isinstance(m, SystemMessage))
    assert "9101:" in system and "(loaded)" in system
    humans = [m.content for m in first_call if isinstance(m, HumanMessage)]
    assert humans[:2] == [EN, "Is the second one open daily?"]
    assert any(isinstance(m, ToolMessage) and "[i3]" in str(m.content) for m in first_call)
    assert any(
        isinstance(m, AIMessage) and m.tool_calls and m.tool_calls[0]["name"] == "load_tools"
        for m in first_call
    )

    # The history survives too, and user_id is the null placeholder.
    history = client.get(f"/v1/conversations/{first['conversation_id']}").json()
    assert history["user_id"] is None
    assert [(t["turn"], t["item_ids"]) for t in history["turns"]] == [
        (1, ["i1", "i3"]),
        (2, ["i3"]),
    ]
    doc = mongo_db["conversations"].find_one({"_id": first["conversation_id"]})
    assert "user_id" in doc and doc["user_id"] is None
    app_2.state.persistence.close()


def test_a_tool_removed_during_the_restart_is_unloaded(mongo_db: Database) -> None:
    registry = _seed_places(mongo_db)
    model_1 = ScriptedChatModel(
        responses=[
            ai("", tool_calls=[tool_call("load_tools", {"source_ids": [9101, 9102]}, "l1")]),
            submit("Loaded.", []),
        ]
    )
    app_1 = _app(mongo_db, model_1)
    first = TestClient(app_1).post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": EN})
    first = first.json()
    app_1.state.persistence.close()

    registry.delete_tool(9102)  # e.g. `geosearch-registry delete 9102` while down
    model_2 = ScriptedChatModel(responses=[submit("Only search is left.", [], "s2")])
    app_2 = _app(mongo_db, model_2)
    second = TestClient(app_2).post(
        "/v1/requests", json={"prompt": "q2", "conversation_id": first["conversation_id"]}
    )
    assert second.status_code == 200, second.text
    system = next(str(m.content) for m in model_2.calls[0] if isinstance(m, SystemMessage))
    assert system.endswith("Tool source_9102 is no longer available and was unloaded.")
    snapshot = app_2.state.runner.agents.current().get_state(
        {"configurable": {"thread_id": first["conversation_id"]}}
    )
    assert snapshot.values["loaded_tools"] == [9101]
    app_2.state.persistence.close()
