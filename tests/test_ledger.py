"""Token-ledger tests, driven by the scripted model so they're deterministic.
The ledger must record one entry per model call, estimate the three token
buckets, carry reported numbers, and flag over-budget and (conservatively)
possible truncation."""

import shapely
from conftest import HANDOFF_POLYGON
from scripted_model import ScriptedChatModel, ai, tool_call

from geosearch.agent.build import build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.ledger import TokenLedger
from geosearch.agent.run import build_new_turn_state, invoke_turn
from geosearch.config import ContextBudgetConfig, GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps

AREA_SUMMARY = "area_test · Polygon · 26.2 km²"


def _run(responses: list, *, budget: ContextBudgetConfig | None = None) -> TokenLedger:
    cfg = GeoConfig(_env_file=None)
    if budget is not None:
        cfg.budget = budget
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    ledger = TokenLedger()
    context = AgentContext(area_ops=ops, cfg=cfg, ledger=ledger)
    agent = build_agent(cfg, ScriptedChatModel(responses=responses))
    state = build_new_turn_state(
        conversation_id="c1",
        area_id=area_id,
        area_wkt=HANDOFF_POLYGON,
        area_summary=AREA_SUMMARY,
        request_id="r1",
        prompt="How big is this area?",
        turn=1,
    )
    invoke_turn(agent, context, state)
    return ledger


def test_one_record_per_model_call() -> None:
    ledger = _run(
        [ai("", tool_calls=[tool_call("geo_describe_area")]), ai("About 26.2 km².")]
    )
    assert len(ledger.records) == 2
    assert [r.call_index for r in ledger.records] == [0, 1]


def test_record_has_token_estimates_and_reported_numbers() -> None:
    ledger = _run([ai("Hi.", tokens=42)])
    record = ledger.records[0]
    # Estimates are populated and add up.
    assert record.est_system_tokens > 0
    assert record.est_tool_tokens > 0  # the default tool schemas are not free
    assert record.est_total == (
        record.est_system_tokens + record.est_tool_tokens + record.est_message_tokens
    )
    # Reported numbers come straight from usage_metadata.
    assert record.reported_input_tokens == 42
    assert record.reported_output_tokens is not None
    assert "geo_describe_area" in record.tools_offered


def test_over_budget_flag_with_tiny_budget() -> None:
    tiny = ContextBudgetConfig(input_budget_tokens=50)  # smaller than any real call
    ledger = _run([ai("Hi.")], budget=tiny)
    assert ledger.records[0].over_budget is True
    assert ledger.summary().over_budget is True


def test_not_over_budget_with_default_budget() -> None:
    ledger = _run([ai("Hi.")])
    assert ledger.records[0].over_budget is False
    assert ledger.summary().over_budget is False


def test_truncation_flag_is_first_call_only() -> None:
    # Tiny reported input (10) vs a much larger estimate trips truncation, but
    # only the first call may be flagged (the conservative rule).
    ledger = _run(
        [ai("", tool_calls=[tool_call("geo_describe_area")], tokens=10), ai("Done.", tokens=10)]
    )
    assert ledger.records[0].possible_truncation is True
    assert all(r.possible_truncation is False for r in ledger.records[1:])


def test_summary_aggregates_calls() -> None:
    ledger = _run(
        [ai("", tool_calls=[tool_call("geo_describe_area")], tokens=100), ai("Answer.", tokens=120)]
    )
    summary = ledger.summary()
    assert summary.model_calls == 2
    assert summary.input_tokens == 220  # 100 + 120 reported
    assert summary.peak_input_tokens == 120
