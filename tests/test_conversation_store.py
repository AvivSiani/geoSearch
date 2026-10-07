"""Conversation records (Stage 6, D4) on both backends: user_id is always null,
only finished turns are recorded, a lost record write is backfilled from the
checkpoint, expiry and missing state are 404s, and the mongodb backend fails
fast when MongoDB is down. MongoDB cases skip when it isn't running."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import HANDOFF_POLYGON
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from persistence_helpers import build_runner, mongo_cfg
from pymongo.database import Database
from pymongo.errors import AutoReconnect
from scripted_model import ScriptedChatModel, ai, submit, tool_call

from geosearch.agent.persistence import Persistence, PersistenceUnavailable, open_persistence
from geosearch.agent.run import RequestRunner
from geosearch.api.app import create_app
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.registry.store import InMemoryRegistry
from geosearch.request.models import UserRequest


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


class FailOnCall(ScriptedChatModel):
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


@pytest.fixture(params=["memory", "mongodb"])
def backend(request: pytest.FixtureRequest) -> Iterator[tuple[GeoConfig, Clock, Persistence]]:
    clock = Clock()
    if request.param == "memory":
        cfg = GeoConfig(_env_file=None)
    else:
        cfg = mongo_cfg(request.getfixturevalue("mongo_db"))
    persistence = open_persistence(cfg, clock=clock)
    yield cfg, clock, persistence
    persistence.close()


def _runner(backend: tuple, responses: list, fail_at: int = -1) -> RequestRunner:
    cfg, clock, persistence = backend
    model = FailOnCall(responses=responses, fail_at=fail_at)
    return build_runner(cfg, persistence, model, clock=clock)


def _turns(n: int) -> list:
    return [
        m
        for i in range(1, n + 1)
        for m in (
            ai("", tool_calls=[tool_call("geo_describe_area", {}, f"g{i}")]),
            submit(f"answer {i}", [], f"s{i}"),
        )
    ]


def test_records_finished_turns(backend: tuple) -> None:
    runner = _runner(backend, _turns(2))
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="How big?"))
    runner.handle(UserRequest(prompt="And again?", conversation_id=first.conversation_id))

    record = runner.registry.live(first.conversation_id)
    assert record.user_id is None
    assert record.area_id == first.state["conversation"]["area_id"]
    assert record.area_summary == first.area_summary
    assert record.turn_count == 2
    one, two = record.turns
    assert (one["turn"], one["prompt"], one["answer"]) == (1, "How big?", "answer 1")
    assert (two["turn"], two["prompt"], two["answer"]) == (2, "And again?", "answer 2")
    assert one["answer_source"] == "submitted" and one["stopped_reason"] == "finished"
    assert one["request_id"] == first.request_id
    assert one["item_ids"] == []
    assert one["tokens"]["model_calls"] == 2 and one["tokens"]["input_tokens"] > 0
    assert one["started_at"] <= one["finished_at"] <= record.updated_at
    assert record.expires_at == record.updated_at + timedelta(
        minutes=backend[0].conversation.idle_ttl_minutes
    )


def test_new_conversation_documents_have_a_null_user_id(mongo_db: Database) -> None:
    cfg = mongo_cfg(mongo_db)
    runner = build_runner(cfg, open_persistence(cfg), ScriptedChatModel(responses=_turns(1)))
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q"))
    doc = mongo_db["conversations"].find_one({"_id": first.conversation_id})
    assert "user_id" in doc and doc["user_id"] is None
    assert mongo_db["areas"].count_documents({"_id": doc["area_id"]}) == 1


def test_a_failed_turn_is_not_recorded(backend: tuple) -> None:
    # Call 2 is turn 2's first model call.
    runner = _runner(backend, _turns(2), fail_at=2)
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    with pytest.raises(RuntimeError):
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert runner.registry.live(first.conversation_id).turn_count == 1

    retry = runner.handle(UserRequest(prompt="q2 again", conversation_id=first.conversation_id))
    assert retry.turn == 2
    turns = runner.registry.live(first.conversation_id).turns
    assert [t["prompt"] for t in turns] == ["q1", "q2 again"]


def test_a_lost_record_write_is_backfilled(backend: tuple) -> None:
    runner = _runner(backend, _turns(3))
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    store = runner.registry._store
    real_append = store.append_turn

    def down_once(*args: Any) -> bool:
        store.append_turn = real_append
        raise AutoReconnect("store went away")

    store.append_turn = down_once
    with pytest.raises(AutoReconnect):  # the route turns this into a 503
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert runner.registry.live(first.conversation_id).turn_count == 1

    third = runner.handle(UserRequest(prompt="q3", conversation_id=first.conversation_id))
    assert third.turn == 3  # turn 2 finished in the checkpoint, so it counts
    turns = runner.registry.live(first.conversation_id).turns
    assert [(t["turn"], t["prompt"]) for t in turns] == [(1, "q1"), (2, "q2"), (3, "q3")]
    recovered = turns[1]
    assert recovered["recovered"] is True and recovered["tokens"] is None
    assert recovered["answer"] == "answer 2" and recovered["answer_source"] == "submitted"
    assert "recovered" not in turns[2]


def test_expired_conversation_is_not_found_and_cleaned_up(backend: tuple) -> None:
    cfg, clock, persistence = backend
    runner = _runner(backend, _turns(2))
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    clock.now += timedelta(minutes=cfg.conversation.idle_ttl_minutes, seconds=1)
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert exc.value.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert persistence.conversations.get(first.conversation_id) is None
    config = {"configurable": {"thread_id": first.conversation_id}}
    assert persistence.checkpointer.get_tuple(config) is None


def test_a_record_without_state_is_not_found(backend: tuple) -> None:
    _, _, persistence = backend
    runner = _runner(backend, _turns(2))
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    persistence.checkpointer.delete_thread(first.conversation_id)  # e.g. expired first
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert exc.value.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert persistence.conversations.get(first.conversation_id) is None


def test_turn_cap_counts_recorded_turns(backend: tuple) -> None:
    cfg, _, _ = backend
    cfg.conversation.max_turns = 1
    runner = _runner(backend, _turns(2))
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert exc.value.code == ErrorCode.CONVERSATION_LIMIT


def test_keep_alive_extends_checkpoints_and_area(mongo_db: Database) -> None:
    clock = Clock()
    cfg = mongo_cfg(mongo_db)
    runner = build_runner(
        cfg, open_persistence(cfg, clock=clock), ScriptedChatModel(responses=_turns(2)),
        clock=clock,
    )  # fmt: skip
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    clock.now += timedelta(days=3)
    runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    expected = clock.now + timedelta(minutes=cfg.conversation.idle_ttl_minutes)
    thread = {"thread_id": first.conversation_id}
    for doc in mongo_db["checkpoints"].find(thread):  # turn 1's checkpoints too
        assert doc["expires_at"].replace(tzinfo=UTC) == expected
    area = mongo_db["areas"].find_one({"_id": first.state["conversation"]["area_id"]})
    assert area["expires_at"].replace(tzinfo=UTC) == expected


def _unreachable(cfg: GeoConfig) -> GeoConfig:
    cfg.conversation.store = "mongodb"
    cfg.mongo.uri = "mongodb://127.0.0.1:1"
    cfg.mongo.server_selection_timeout_ms = 200
    return cfg


def test_mongodb_backend_fails_fast_when_down() -> None:
    with pytest.raises(PersistenceUnavailable):
        open_persistence(_unreachable(GeoConfig(_env_file=None)))


def test_app_never_falls_back_to_memory() -> None:
    with pytest.raises(PersistenceUnavailable):
        create_app(
            _unreachable(GeoConfig(_env_file=None)),
            model=ScriptedChatModel(responses=[ai("x")]),
            tool_registry=InMemoryRegistry(),
        )
