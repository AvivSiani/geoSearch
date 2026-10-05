"""Harness tests: the trimmed harness offers only the expected tools, and that
trimming is what cuts the tool-schema token cost versus the default harness."""

import shapely
from conftest import HANDOFF_POLYGON
from scripted_model import CapturingChatModel, ai

from geosearch.agent.build import TRIMMED_TOOLS, build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.ledger import TokenLedger
from geosearch.agent.run import build_new_turn_state, invoke_turn
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps


def _run(harness: str) -> tuple[list[str], TokenLedger]:
    cfg = GeoConfig(_env_file=None)
    cfg.agent.harness = harness
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    ledger = TokenLedger()
    context = AgentContext(area_ops=ops, cfg=cfg, ledger=ledger)
    model = CapturingChatModel(responses=[ai("done")])
    agent = build_agent(cfg, model)
    state = build_new_turn_state(
        conversation_id="c1",
        area_id=area_id,
        area_wkt=HANDOFF_POLYGON,
        area_summary="S",
        request_id="r1",
        prompt="q",
        turn=1,
    )
    invoke_turn(agent, context, state)
    return model.last_bound_tools, ledger


def test_trimmed_harness_offers_only_expected_tools() -> None:
    tools, _ = _run("trimmed")
    assert set(tools) == TRIMMED_TOOLS


def test_default_harness_offers_more_tools() -> None:
    tools, _ = _run("default")
    # The default suite adds the heavy file tools and the subagent `task` tool.
    assert {"edit_file", "glob", "grep", "task"} <= set(tools)
    assert "geo_describe_area" in tools


def test_trimming_cuts_tool_schema_tokens() -> None:
    _, trimmed = _run("trimmed")
    _, default = _run("default")
    trimmed_tool_tokens = trimmed.records[0].est_tool_tokens
    default_tool_tokens = default.records[0].est_tool_tokens
    # Fewer tool schemas -> materially fewer tokens (the point of the trim).
    assert trimmed_tool_tokens < default_tool_tokens
    assert trimmed_tool_tokens < default_tool_tokens * 0.5
