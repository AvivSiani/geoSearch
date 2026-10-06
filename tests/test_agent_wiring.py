"""Scripted-model wiring tests for the single-turn agent: the geo tool is
reachable and feeds the answer, WKT never reaches the model, the area line is
on every system message, and the call limit stops the run."""

import shapely
from conftest import HANDOFF_POLYGON
from langchain_core.messages import SystemMessage
from scripted_model import ScriptedChatModel, ai, submit, tool_call

from geosearch.agent.build import build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.prompts import SYSTEM_PROMPT
from geosearch.agent.run import build_new_turn_state, invoke_turn
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps

AREA_SUMMARY = "area_test · Polygon · 26.2 km² · 34.750–34.800 E, 32.050–32.100 N"


def _fixture(cfg: GeoConfig | None = None) -> tuple[AgentContext, str, str]:
    cfg = cfg or GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    return AgentContext(area_ops=ops, cfg=cfg), area_id, HANDOFF_POLYGON


def _state(area_id: str, wkt: str, prompt: str) -> dict:
    return build_new_turn_state(
        conversation_id="c1",
        area_id=area_id,
        area_wkt=wkt,
        area_summary=AREA_SUMMARY,
        request_id="r1",
        prompt=prompt,
        turn=1,
    )


def test_wiring_calls_geo_tool_and_returns_its_text() -> None:
    context, area_id, wkt = _fixture()
    model = ScriptedChatModel(
        responses=[
            ai("", tool_calls=[tool_call("geo_describe_area")]),
            ai("This area is about 26.2 km²."),
        ]
    )
    agent = build_agent(context.cfg, model)
    outcome = invoke_turn(agent, context, _state(area_id, wkt, "How big is this area?"))

    assert outcome.stopped_reason == "finished"
    assert "26.2" in outcome.answer
    # The tool result (with the real computed size) must be present in the thread.
    tool_results = [m for m in outcome.state["messages"] if m.type == "tool"]
    assert tool_results and "km²" in tool_results[0].content


def test_no_wkt_reaches_the_model_or_tool_results() -> None:
    context, area_id, wkt = _fixture()
    model = ScriptedChatModel(
        responses=[
            ai("", tool_calls=[tool_call("geo_describe_area")]),
            ai("Done."),
        ]
    )
    agent = build_agent(context.cfg, model)
    outcome = invoke_turn(agent, context, _state(area_id, wkt, "Describe it."))

    # Nothing the model ever saw may contain the WKT coordinate string.
    needle = "34.75 32.05"
    for call in model.calls:
        for message in call:
            assert needle not in str(message.content)
    # Nor any tool result or the final answer.
    for message in outcome.state["messages"]:
        assert needle not in str(message.content)


def test_area_line_on_every_call_and_system_prompt_survives() -> None:
    context, area_id, wkt = _fixture()
    model = ScriptedChatModel(
        responses=[
            ai("", tool_calls=[tool_call("geo_describe_area")]),
            submit("Answer."),
        ]
    )
    agent = build_agent(context.cfg, model)
    invoke_turn(agent, context, _state(area_id, wkt, "q"))

    assert len(model.calls) == 2
    for call in model.calls:
        system = next(m for m in call if isinstance(m, SystemMessage))
        # The area line is appended on every call...
        assert f"Current area: {AREA_SUMMARY}" in system.content
        # ...without clobbering the existing system prompt: all of it survives,
        # with our area line after it (append, not replace). In deepagents
        # 0.7.21 the harness "base prompt" lives in tool schemas, not the system
        # message, so the system text here is exactly our prompt + the area line,
        # followed by the tool catalog (Stage 4; empty registry here).
        assert system.content == (
            f"{SYSTEM_PROMPT}\nCurrent area: {AREA_SUMMARY}\n\nNo tools available."
        )


def test_call_limit_stops_the_run() -> None:
    cfg = GeoConfig(_env_file=None)
    cfg.agent.max_model_calls_per_turn = 3
    context, area_id, wkt = _fixture(cfg)
    # A model that always wants to call a tool: it can never finish on its own.
    model = ScriptedChatModel(responses=[ai("", tool_calls=[tool_call("geo_describe_area")])])
    agent = build_agent(cfg, model)
    outcome = invoke_turn(agent, context, _state(area_id, wkt, "loop forever"))

    assert outcome.stopped_reason == "call_limit"
    assert len(model.calls) == 3
