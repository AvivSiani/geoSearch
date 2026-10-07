"""Conversation tests, all scripted (no model). They cover the multi-turn flow,
area binding and re-put, working-memory seeds, and the three conversation-level
errors (busy, limit, expiry)."""

import json

import pytest
import shapely
from conftest import HANDOFF_POLYGON, TLV_POINT
from scripted_model import ScriptedChatModel, ai, submit

from geosearch.agent.conversations import ConversationRegistry, make_checkpointer
from geosearch.agent.holder import AgentHolder
from geosearch.agent.run import RequestRunner
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.request.models import UserRequest

OTHER_POLYGON = "POLYGON((0 0, 0.01 0, 0.01 0.01, 0 0.01, 0 0))"


class FakeClock:
    """A manually advanced clock, so idle-TTL expiry is testable without sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _runner(
    *, responses: list | None = None, max_turns: int = 20, clock=None
) -> RequestRunner:
    cfg = GeoConfig(_env_file=None)
    cfg.conversation.max_turns = max_turns
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    buffer_strategy = STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)
    checkpointer = make_checkpointer(cfg)
    kwargs = {"clock": clock} if clock is not None else {}
    registry = ConversationRegistry(cfg.conversation, checkpointer, **kwargs)
    model = ScriptedChatModel(responses=responses or [ai("ok")])
    agents = AgentHolder(cfg, model, checkpointer)
    return RequestRunner(cfg, agents, registry, store, ops, buffer_strategy)


def test_two_turn_conversation_increments_and_remembers() -> None:
    runner = _runner(
        responses=[submit("turn 1 answer"), submit("turn 2 answer", call_id="submit_2")]
    )
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="How big is this area?"))
    assert first.turn == 1

    second = runner.handle(
        UserRequest(prompt="And again?", conversation_id=first.conversation_id)
    )
    assert second.turn == 2
    assert second.conversation_id == first.conversation_id
    # Turn 2 sees turn 1's messages: 3 per turn (prompt, submit call, its result).
    assert len(second.state["messages"]) == 6
    assert (first.answer, second.answer) == ("turn 1 answer", "turn 2 answer")


def test_language_is_detected_per_turn() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="Asian restaurants"))
    assert first.state["request"]["language"] == "en"
    # A follow-up may switch language; it is re-detected, never carried over.
    second = runner.handle(
        UserRequest(prompt="ומה עם בתי קפה?", conversation_id=first.conversation_id)
    )
    assert second.state["request"]["language"] == "he"


def test_working_memory_seeds_written() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    files = first.state["files"]
    assert "/conversation.json" in files
    assert "/turns/1/request.json" in files
    assert json.loads(files["/turns/1/request.json"]["content"])["prompt"] == "q1"

    second = runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    files2 = second.state["files"]
    # Turn 2's file is added; turn 1's is still present; conversation.json refreshed.
    assert "/turns/2/request.json" in files2
    assert "/turns/1/request.json" in files2
    assert json.loads(files2["/conversation.json"]["content"])["turn"] == 2


def test_follow_up_without_wkt_works() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    second = runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert second.answer == "a2"


def test_follow_up_with_matching_wkt_is_accepted() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    second = runner.handle(
        UserRequest(wkt=HANDOFF_POLYGON, prompt="q2", conversation_id=first.conversation_id)
    )
    assert second.turn == 2


def test_follow_up_with_different_wkt_is_area_mismatch() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(
            UserRequest(wkt=OTHER_POLYGON, prompt="q2", conversation_id=first.conversation_id)
        )
    assert exc.value.code == ErrorCode.AREA_MISMATCH
    # A rejected follow-up must not consume a turn.
    ok = runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert ok.turn == 2


def test_follow_up_survives_area_store_eviction() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="How big?"))
    # Simulate the LRU evicting everything.
    runner.store._areas.clear()
    # The follow-up re-puts the stored WKT, so geo_describe_area still works.
    second = runner.handle(UserRequest(prompt="again?", conversation_id=first.conversation_id))
    assert second.turn == 2
    area_id = first.state["conversation"]["area_id"]
    assert shapely.from_wkt(HANDOFF_POLYGON).equals(runner.store.get(area_id))


def test_follow_up_after_eviction_keeps_a_seven_decimal_area() -> None:
    # 7th-decimal coordinates: a 6-decimal WKT round-trip would hash to another
    # area_id (Stage 6 §1.2). Full-precision WKT restores the exact same area.
    precise = (
        "POLYGON((34.7500004 32.0500007, 34.8000003 32.05, 34.8 32.1000006, "
        "34.75 32.1, 34.7500004 32.0500007))"
    )
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=precise, prompt="q1"))
    area_id = first.state["conversation"]["area_id"]
    original = runner.store.get(area_id)
    runner.store._areas.clear()

    second = runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert second.state["conversation"]["area_id"] == area_id
    assert shapely.equals_exact(runner.store.get(area_id), original, tolerance=0)


def test_unknown_conversation_is_not_found() -> None:
    runner = _runner()
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(UserRequest(prompt="q", conversation_id="does-not-exist"))
    assert exc.value.code == ErrorCode.CONVERSATION_NOT_FOUND


def test_conversation_busy_when_lock_held() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    # Simulate a turn already running by holding the conversation's lock.
    entry = runner.registry._entries[first.conversation_id]
    entry.lock.acquire()
    try:
        with pytest.raises(GeoValidationError) as exc:
            runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
        assert exc.value.code == ErrorCode.CONVERSATION_BUSY
    finally:
        entry.lock.release()


def test_conversation_limit_reached() -> None:
    runner = _runner(responses=[ai("a1"), ai("a2")], max_turns=1)
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    assert first.turn == 1
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert exc.value.code == ErrorCode.CONVERSATION_LIMIT


def test_idle_expiry_is_not_found() -> None:
    clock = FakeClock()
    runner = _runner(responses=[ai("a1"), ai("a2")], clock=clock)
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    # Advance just past the idle TTL.
    clock.now += GeoConfig(_env_file=None).conversation.idle_ttl_minutes * 60 + 1
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert exc.value.code == ErrorCode.CONVERSATION_NOT_FOUND


def test_follow_up_buffer_not_applicable() -> None:
    runner = _runner()
    first = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt="q1"))
    with pytest.raises(GeoValidationError) as exc:
        runner.handle(
            UserRequest(
                prompt="q2", conversation_id=first.conversation_id, point_buffer_m=100.0
            )
        )
    assert exc.value.code == ErrorCode.BUFFER_NOT_APPLICABLE


def test_point_area_follow_up_rebuffers_consistently() -> None:
    # A Point buffers to a circle on turn 1; a follow-up keeps that same area.
    runner = _runner(responses=[ai("a1"), ai("a2")])
    first = runner.handle(UserRequest(wkt=TLV_POINT, prompt="q1"))
    second = runner.handle(UserRequest(prompt="q2", conversation_id=first.conversation_id))
    assert second.state["conversation"]["area_id"] == first.state["conversation"]["area_id"]
