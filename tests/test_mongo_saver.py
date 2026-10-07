"""MongoCheckpointSaver (Stage 6, D1), on real MongoDB (skips when it's down).

The parity suite runs the same scripted conversations through RequestRunner
on InMemorySaver and on ours and requires the same reloaded state. It covers
both durability modes, the places flow (parallel tool loads, summarizer, items,
working-memory files), a turn that fails mid-way, and a long conversation that
crosses a DeltaChannel snapshot (messages and files are delta channels).
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import HANDOFF_POLYGON
from fake_registry import places_catalog
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pymongo.database import Database
from scripted_model import ScriptedChatModel, ai, submit, tool_call

from geosearch.agent.conversations import ConversationRegistry
from geosearch.agent.holder import AgentHolder
from geosearch.agent.mongo_saver import MongoCheckpointSaver
from geosearch.agent.run import RequestRunner
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.request.models import UserRequest


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


def _saver(db: Database, clock: Clock | None = None) -> MongoCheckpointSaver:
    cfg = GeoConfig(_env_file=None).conversation
    return MongoCheckpointSaver.from_config(db, cfg, clock=clock or Clock())


# -- parity ------------------------------------------------------------------


class FailOnce(ScriptedChatModel):
    """Raises on call number `fail_at` (0-based), then carries on scripted."""

    fail_at: int = -1

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if len(self.calls) == self.fail_at:
            self.calls.append(list(messages))
            raise RuntimeError("model blew up")
        return super()._generate(messages, stop, run_manager, **kwargs)


@dataclass
class Scenario:
    main: list
    summarizer: list
    prompts: list[str]
    fail_at: int = -1


def _places_turn(n: int) -> list:
    return [
        ai("", tool_calls=[tool_call("load_tools", {"source_ids": [9101, 9102]}, f"l{n}")]),
        ai("", tool_calls=[tool_call(
            "source_9101", {"query": "asian restaurant", "max_price": "moderate"}, f"s{n}")]),
        ai("", tool_calls=[tool_call("source_9102", {"item_id": "i1"}, f"d{n}")]),
        submit(f"Answer {n}: Lotus [i1] and Saigon [i3].", ["i1", "i3"], f"sub{n}"),
    ]  # fmt: skip


def _describe_turn(n: int) -> list:
    return [
        ai("", tool_calls=[tool_call("geo_describe_area", {}, f"g{n}")]),
        submit(f"Area answer {n}.", [], f"sub{n}"),
    ]


SUMMARIES = [ai("Lotus [i1], Saigon [i3]."), ai("[i1] open daily.")] * 3

SCENARIOS = {
    "places": Scenario(
        _places_turn(1) + _places_turn(2) + _describe_turn(3), SUMMARIES, ["q1", "q2", "q3"]
    ),
    # Turn 2 dies on its first model call (call index 2); the retry succeeds.
    "failed_turn": Scenario(
        _describe_turn(1) + _describe_turn(2), [ai("x")], ["q1", "q2", "q2 again"], fail_at=2
    ),
    # Enough message updates to cross the delta snapshot (snapshot_frequency=50).
    "long": Scenario(
        [m for n in range(1, 16) for m in _describe_turn(n)], [ai("x")],
        [f"q{n}" for n in range(1, 16)],
    ),
}  # fmt: skip


def _run(scenario: Scenario, saver: BaseCheckpointSaver, durability: str) -> dict:
    """Run every prompt in one conversation; return the reloaded final state."""
    cfg = GeoConfig(_env_file=None)
    cfg.conversation.checkpoint_durability = durability
    main = FailOnce(responses=scenario.main, fail_at=scenario.fail_at)
    summarizer = ScriptedChatModel(responses=scenario.summarizer)
    store = InMemoryAreaStore(max_entries=10)
    agents = AgentHolder(cfg, main, saver, places_catalog(), summarizer)
    runner = RequestRunner(
        cfg, agents, ConversationRegistry(cfg.conversation, saver), store, AreaOps(store),
        STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer),
    )  # fmt: skip
    conversation_id = None
    for prompt in scenario.prompts:
        req = (
            UserRequest(wkt=HANDOFF_POLYGON, prompt=prompt)
            if conversation_id is None
            else UserRequest(prompt=prompt, conversation_id=conversation_id)
        )
        try:
            conversation_id = runner.handle(req).conversation_id
        except RuntimeError:
            assert conversation_id is not None  # only a follow-up is scripted to fail
    snapshot = agents.current().get_state({"configurable": {"thread_id": conversation_id}})
    return {"values": _comparable(snapshot.values), "next": snapshot.next}


def _comparable(values: dict) -> dict:
    """Drop what differs between any two runs: random message ids, file
    timestamps, the generated conversation and request ids."""
    out = dict(values)
    out["messages"] = [
        (m.type, m.content, [(c["name"], c["args"]) for c in getattr(m, "tool_calls", [])])
        for m in values["messages"]
    ]
    out["files"] = {
        path: re.sub(r'"request_id": "\w+"', '"request_id": "-"', f["content"])
        for path, f in values.get("files", {}).items()
    }
    out["conversation"] = {
        k: v for k, v in values["conversation"].items() if k != "conversation_id"
    }
    out["request"] = {k: v for k, v in values["request"].items() if k != "request_id"}
    out.pop("thread_model_call_count", None)
    return out


@pytest.mark.parametrize("durability", ["exit", "sync"])
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_parity_with_in_memory_saver(mongo_db: Database, name: str, durability: str) -> None:
    scenario = SCENARIOS[name]
    expected = _run(scenario, InMemorySaver(), durability)
    actual = _run(scenario, _saver(mongo_db), durability)
    assert actual == expected
    assert expected["values"]["messages"]  # sanity: the comparison isn't vacuous


def test_long_conversation_reloads_on_a_fresh_saver(mongo_db: Database) -> None:
    """A second saver instance on the same collections (a restart) rebuilds the
    delta channels from the stored chain alone."""
    first = _saver(mongo_db)
    expected = _run(SCENARIOS["long"], first, "sync")
    thread_id = mongo_db["checkpoints"].find_one()["thread_id"]
    agents = AgentHolder(
        GeoConfig(_env_file=None), ScriptedChatModel(responses=[ai("x")]), _saver(mongo_db)
    )
    snapshot = agents.current().get_state({"configurable": {"thread_id": thread_id}})
    assert _comparable(snapshot.values)["messages"] == expected["values"]["messages"]
    assert len(expected["values"]["messages"]) == 15 * 5  # prompt, call, result, submit, result


def test_exit_durability_writes_few_checkpoints(mongo_db: Database) -> None:
    _run(SCENARIOS["places"], _saver(mongo_db), "exit")
    exit_count = mongo_db["checkpoints"].count_documents({})
    mongo_db["checkpoints"].delete_many({})
    _run(SCENARIOS["places"], _saver(mongo_db), "sync")
    sync_count = mongo_db["checkpoints"].count_documents({})
    assert exit_count <= 2 * 3  # 1-2 per turn
    assert sync_count > 5 * exit_count


# -- the contract, directly ----------------------------------------------------


def _config(thread: str = "t1", checkpoint_id: str | None = None) -> dict:
    configurable = {"thread_id": thread, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(checkpoint_id: str) -> dict:
    return {
        "v": 1, "id": checkpoint_id, "ts": "2026-10-07T00:00:00+00:00",
        "channel_values": {"x": checkpoint_id}, "channel_versions": {"x": checkpoint_id},
        "versions_seen": {},
    }  # fmt: skip


def _put_chain(saver: MongoCheckpointSaver, thread: str, ids: list[str]) -> None:
    parent: str | None = None
    for checkpoint_id in ids:
        saver.put(
            _config(thread, parent), _checkpoint(checkpoint_id), {"step": int(checkpoint_id)}, {}
        )
        parent = checkpoint_id


def test_latest_parent_and_exact_lookup(mongo_db: Database) -> None:
    saver = _saver(mongo_db)
    _put_chain(saver, "t1", ["1", "2", "3"])
    latest = saver.get_tuple(_config("t1"))
    assert latest.checkpoint["id"] == "3"
    assert latest.parent_config["configurable"]["checkpoint_id"] == "2"
    assert saver.get_tuple(_config("t1", "1")).parent_config is None
    assert saver.get_tuple(_config("nope")) is None


def test_list_filter_before_limit(mongo_db: Database) -> None:
    saver = _saver(mongo_db)
    _put_chain(saver, "t1", ["1", "2", "3", "4"])
    _put_chain(saver, "t2", ["5"])
    ids = lambda tuples: [t.checkpoint["id"] for t in tuples]  # noqa: E731
    assert ids(saver.list(_config("t1"))) == ["4", "3", "2", "1"]
    assert ids(saver.list(_config("t1"), limit=2)) == ["4", "3"]
    assert ids(saver.list(_config("t1"), before=_config("t1", "3"))) == ["2", "1"]
    assert ids(saver.list(_config("t1"), filter={"step": 2})) == ["2"]
    assert ids(saver.list(None)) == ["5", "4", "3", "2", "1"]


def test_writes_keep_order_and_special_writes_overwrite(mongo_db: Database) -> None:
    saver = _saver(mongo_db)
    _put_chain(saver, "t1", ["1"])
    config = _config("t1", "1")
    saver.put_writes(config, [("a", 1), ("b", 2)], "task")
    saver.put_writes(config, [("a", 99)], "task")  # same (task, idx 0): kept once
    saver.put_writes(config, [("c", 3)], "other")
    saver.put_writes(config, [("__error__", "e1")], "task")
    saver.put_writes(config, [("__error__", "e2")], "task")  # special: replaced
    writes = saver.get_tuple(config).pending_writes
    assert writes == [
        ("task", "a", 1),
        ("task", "b", 2),
        ("other", "c", 3),
        ("task", "__error__", "e2"),
    ]


def test_touch_refreshes_the_whole_thread(mongo_db: Database) -> None:
    clock = Clock()
    saver = _saver(mongo_db, clock)
    _put_chain(saver, "t1", ["1", "2"])
    saver.put_writes(_config("t1", "2"), [("a", 1)], "task")
    _put_chain(saver, "t2", ["9"])
    clock.now += timedelta(days=3)
    saver.touch("t1")
    expected = clock.now + timedelta(
        minutes=GeoConfig(_env_file=None).conversation.idle_ttl_minutes
    )
    for collection in (mongo_db["checkpoints"], mongo_db["checkpoint_writes"]):
        for doc in collection.find({"thread_id": "t1"}):
            assert doc["expires_at"].replace(tzinfo=UTC) == expected
    t2 = mongo_db["checkpoints"].find_one({"thread_id": "t2"})
    assert t2["expires_at"].replace(tzinfo=UTC) < expected


def test_delete_thread(mongo_db: Database) -> None:
    saver = _saver(mongo_db)
    _put_chain(saver, "t1", ["1"])
    saver.put_writes(_config("t1", "1"), [("a", 1)], "task")
    _put_chain(saver, "t2", ["2"])
    saver.delete_thread("t1")
    assert saver.get_tuple(_config("t1")) is None
    assert mongo_db["checkpoint_writes"].count_documents({"thread_id": "t1"}) == 0
    assert saver.get_tuple(_config("t2")) is not None


def test_ttl_indexes_and_idempotent_setup(mongo_db: Database) -> None:
    _saver(mongo_db)
    _saver(mongo_db)  # a second start must not conflict
    for name in ("checkpoints", "checkpoint_writes"):
        ttl = [i for i in mongo_db[name].list_indexes() if "expireAfterSeconds" in i]
        assert [(dict(i["key"]), i["expireAfterSeconds"]) for i in ttl] == [({"expires_at": 1}, 0)]


def test_large_checkpoint_warns(mongo_db: Database, caplog: pytest.LogCaptureFixture) -> None:
    cfg = GeoConfig(_env_file=None).conversation.model_copy(update={"checkpoint_warn_bytes": 100})
    saver = MongoCheckpointSaver.from_config(mongo_db, cfg)
    big = _checkpoint("1") | {"channel_values": {"x": "y" * 500}}
    with caplog.at_level(logging.WARNING):
        saver.put(_config("t1"), big, {}, {})
    assert "warn above 100" in caplog.text
    assert saver.get_tuple(_config("t1")).checkpoint["channel_values"]["x"] == "y" * 500
