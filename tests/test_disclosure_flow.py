"""Stage 4 step 6: progressive disclosure end to end, with a scripted model, a
fake registry (scale fixture + demo) and isolated handlers. No MongoDB.

Each test drives full turns through RequestRunner + AgentHolder, so the agent
is the real one: real middleware stack, real ToolNode, real checkpointer.
"""

import json
from dataclasses import dataclass, field

import pytest
from conftest import HANDOFF_POLYGON
from fake_registry import DEMO_DEFINITION, FakeRegistry, scale_catalog, scale_registry
from langchain_core.messages import SystemMessage, ToolMessage
from scripted_model import ScriptedChatModel, ai, tool_call

from geosearch.agent.context import AgentContext
from geosearch.agent.conversations import ConversationRegistry, make_checkpointer
from geosearch.agent.disclosure import CATALOG_HEADER
from geosearch.agent.holder import AgentHolder
from geosearch.agent.ledger import TokenLedger
from geosearch.agent.run import RequestRunner, TurnOutcome
from geosearch.config import DisclosureConfig, GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.request.models import UserRequest

REGISTRY_IDS = [*range(101, 111), 9001]


@dataclass
class RecordingRunner(RequestRunner):
    """Keeps each turn's AgentContext so tests can read its token ledger."""

    contexts: list[AgentContext] = field(default_factory=list)

    def _context(self) -> AgentContext:
        context = AgentContext(area_ops=self.ops, cfg=self.cfg, ledger=TokenLedger())
        self.contexts.append(context)
        return context

    def offered(self, turn_index: int = -1) -> list[list[str]]:
        """Registry tools offered on each model call of a turn, from the ledger."""
        records = self.contexts[turn_index].ledger.records
        return [sorted(n for n in r.tools_offered if n.startswith("source_")) for r in records]


def _runner(
    responses: list,
    registry: FakeRegistry | None = None,
    cfg: GeoConfig | None = None,
) -> tuple[RecordingRunner, ScriptedChatModel, FakeRegistry]:
    cfg = cfg or GeoConfig(_env_file=None)
    registry = registry or scale_registry()
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    checkpointer = make_checkpointer(cfg)
    model = ScriptedChatModel(responses=responses)
    summarizer = ScriptedChatModel(responses=[ai("Summary.")])  # keeps the script apart
    agents = AgentHolder(cfg, model, checkpointer, scale_catalog(registry), summarizer)
    runner = RecordingRunner(
        cfg,
        agents,
        ConversationRegistry(cfg.conversation, checkpointer),
        store,
        AreaOps(store),
        STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer),
    )
    return runner, model, registry


def _first(runner: RequestRunner, prompt: str = "q") -> TurnOutcome:
    return runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt=prompt))


def _next(runner: RequestRunner, first: TurnOutcome, prompt: str = "q2") -> TurnOutcome:
    return runner.handle(UserRequest(prompt=prompt, conversation_id=first.conversation_id))


def _load(*ids: int, call_id: str = "load_1") -> object:
    return ai("", tool_calls=[tool_call("load_tools", {"source_ids": list(ids)}, call_id)])


def _call(name: str, args: dict | None = None, call_id: str = "use_1") -> object:
    return ai("", tool_calls=[tool_call(name, args or {}, call_id)])


def _tool_messages(outcome: TurnOutcome) -> list[ToolMessage]:
    return [m for m in outcome.state["messages"] if isinstance(m, ToolMessage)]


# --- the catalog and the filter ---------------------------------------------------


def test_catalog_renders_and_no_registry_schema_before_any_load() -> None:
    runner, model, _ = _runner([ai("Answer.")])
    _first(runner)
    system = next(m for m in model.calls[0] if isinstance(m, SystemMessage))
    assert CATALOG_HEADER in system.content
    for source_id in REGISTRY_IDS:
        assert f"\n{source_id}: " in system.content
    assert runner.offered() == [[]]
    record = runner.contexts[-1].ledger.records[0]
    assert record.est_registry_tool_tokens == 0
    assert record.est_catalog_tokens > 0 and not record.catalog_over_warn


def test_loading_two_ids_offers_exactly_those_from_the_next_call() -> None:
    runner, _, _ = _runner([_load(9001, 101), ai("Done.")])
    outcome = _first(runner)
    assert runner.offered() == [[], ["source_101", "source_9001"]]
    assert outcome.state["loaded_tools"] == [9001, 101]
    records = runner.contexts[-1].ledger.records
    assert records[0].est_registry_tool_tokens == 0 < records[1].est_registry_tool_tokens
    message = _tool_messages(outcome)[0].content
    assert "source_9001 (9001)" in message and "→ returns: lon, lat" in message


def test_loaded_tool_runs_and_writes_its_result_file() -> None:
    runner, _, _ = _runner([_load(9001), _call("source_9001", {"count": 5}), ai("Here.")])
    outcome = _first(runner)
    result = _tool_messages(outcome)[-1]
    assert "Sampled 5 random point(s)" in result.content
    assert "/turns/" not in result.content  # Stage 5 D3: the path is never shown
    points = json.loads(outcome.state["files"]["/turns/1/results/9001/1.json"]["content"])
    assert len(points) == 5


# --- load_tools cases, through the graph ------------------------------------------


def test_unknown_and_already_loaded_ids_leave_state_unchanged() -> None:
    runner, _, _ = _runner([_load(9001), _load(9001, 99, call_id="load_2"), ai("Done.")])
    outcome = _first(runner)
    assert outcome.state["loaded_tools"] == [9001]
    second = _tool_messages(outcome)[1].content
    assert second == "- 9001 already loaded\nUnknown ids: 99. Use ids from the catalog."


def test_cap_when_set() -> None:
    cfg = GeoConfig(_env_file=None).model_copy(
        update={"disclosure": DisclosureConfig(max_loaded_tools=1)}
    )
    runner, _, _ = _runner([_load(9001, 101), ai("Done.")], cfg=cfg)
    outcome = _first(runner)
    assert outcome.state["loaded_tools"] == [9001]
    assert _tool_messages(outcome)[0].content.endswith("Not loaded (limit 1 reached): 101")
    assert runner.offered() == [[], ["source_9001"]]


# --- the guard ----------------------------------------------------------------------


def test_guard_blocks_unloaded_calls_and_runs_nothing() -> None:
    runner, _, _ = _runner([_call("source_9001", {"count": 3}), ai("Sorry.")])
    outcome = _first(runner)
    blocked = _tool_messages(outcome)[0]
    assert blocked.content == "source_9001 is not loaded. Call load_tools([9001]) first."
    assert blocked.status == "error"
    assert not any("/results/" in path for path in outcome.state["files"])
    assert outcome.state["loaded_tools"] == []


# --- across turns and registry changes ----------------------------------------------


def test_loaded_tools_carry_into_turn_two() -> None:
    runner, _, _ = _runner(
        [
            _load(9001),
            _call("source_9001", {"count": 5}),
            ai("Five points."),
            _call("source_9001", {"count": 3, "seed": 2}, call_id="use_2"),
            ai("Three more."),
        ]
    )
    first = _first(runner)
    second = _next(runner, first, "Now 3 more with seed 2")
    assert second.state["loaded_tools"] == [9001]
    assert runner.offered()[0] == ["source_9001"]  # offered from turn 2's first call
    calls = [m.name for m in _tool_messages(second)[len(_tool_messages(first)) :]]
    assert calls == ["source_9001"]  # no reload
    assert "/turns/2/results/9001/1.json" in second.state["files"]


def test_tool_added_to_registry_works_on_next_request() -> None:
    registry = scale_registry(include=[101])
    runner, model, _ = _runner(
        [ai("Can't."), _load(9001), _call("source_9001", {"count": 2}), ai("Two.")],
        registry=registry,
    )
    first = _first(runner)
    first_system = next(m for m in model.calls[0] if isinstance(m, SystemMessage)).content
    assert "9001:" not in first_system

    registry.upsert_tool(DEMO_DEFINITION)  # a CLI seed, say; no change under agent/
    second = _next(runner, first)
    assert second.state["loaded_tools"] == [9001]
    assert "Sampled 2 random point(s)" in _tool_messages(second)[-1].content


def test_tool_deleted_while_loaded_does_not_break_the_run() -> None:
    registry = scale_registry()
    runner, _, _ = _runner(
        [
            _load(9001, 101),
            ai("Loaded."),
            _call("source_9001", {"count": 2}, call_id="use_2"),
            ai("That tool is gone."),
        ],
        registry=registry,
    )
    first = _first(runner)
    registry.delete_tool(9001)
    second = _next(runner, first)

    assert second.stopped_reason == "finished"
    assert second.answer == "That tool is gone."
    assert runner.offered()[0] == ["source_101"]  # the deleted tool is no longer offered
    error = _tool_messages(second)[-1]
    assert error.status == "error" and "source_9001" in error.content


@pytest.mark.parametrize("ids", [[9001], [101, 105]])
def test_model_never_sees_wkt_with_registry_tools(ids: list[int]) -> None:
    runner, model, _ = _runner([_load(*ids), ai("Done.")])
    _first(runner)
    for call in model.calls:
        for message in call:
            assert "POLYGON" not in str(message.content)
