"""Stage 5 step 6: submit_answer, the one reminder, the fallback, and response
items built from data. Scripted model; items are seeded straight into state."""

import shapely
from conftest import HANDOFF_POLYGON
from langchain_core.messages import HumanMessage, ToolMessage
from scripted_model import ScriptedChatModel, ai, submit, tool_call

from geosearch.agent.answer import REMINDER, build_items, final_answer
from geosearch.agent.build import build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.conversations import make_checkpointer
from geosearch.agent.prompts import SYSTEM_PROMPT
from geosearch.agent.run import TurnOutcome, _last_ai_text, build_new_turn_state, invoke_turn
from geosearch.config import AgentConfig, GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps

ITEMS = {
    "i1": {"source_id": 9101, "ref": "ChIJ-A",
           "row": {"name": "Lotus", "lon": 34.77, "lat": 32.07, "rating": 4.6, "phone": None}},
    "i2": {"source_id": 9101, "ref": "ChIJ-B",
           "row": {"name": "Bamboo", "lon": 34.78, "lat": 32.08, "rating": 4.4}},
}  # fmt: skip


def _turn(responses: list, cfg: GeoConfig | None = None) -> tuple[TurnOutcome, ScriptedChatModel]:
    cfg = cfg or GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=10)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    model = ScriptedChatModel(responses=responses)
    agent = build_agent(cfg, model, make_checkpointer(cfg))
    state = build_new_turn_state(
        conversation_id="c1", area_id=area_id, area_wkt=HANDOFF_POLYGON, area_summary="S",
        request_id="r1", prompt="Asian food?", turn=1,
    )  # fmt: skip
    state["items"] = ITEMS
    context = AgentContext(area_ops=AreaOps(store), cfg=cfg)
    return invoke_turn(agent, context, state, thread_id="c1"), model


def _tool_messages(outcome: TurnOutcome) -> list[ToolMessage]:
    return [m for m in outcome.state["messages"] if isinstance(m, ToolMessage)]


def test_submit_ends_the_turn_and_builds_items_from_data() -> None:
    outcome, model = _turn([submit("Try Lotus [i1] or Bamboo [i2].", ["i1", "i2"])])
    assert len(model.calls) == 1  # no model call after a successful submit
    assert outcome.answer_source == "submitted"
    assert outcome.answer == "Try Lotus [i1] or Bamboo [i2]."
    first = outcome.items[0]
    assert (first.id, first.source_id, first.name, first.lon, first.lat) == (
        "i1", 9101, "Lotus", 34.77, 32.07,
    )  # fmt: skip
    assert first.data == {"rating": 4.6}  # no Nones, no ref
    assert "ChIJ" not in first.model_dump_json()


def test_unknown_ids_are_rejected_and_can_be_fixed() -> None:
    outcome, model = _turn(
        [submit("Ghost [i9].", ["i9"]), submit("Lotus [i1].", ["i1"], call_id="submit_2")]
    )
    rejected = _tool_messages(outcome)[0]
    assert rejected.status == "error" and "unknown item ids: i9" in rejected.content
    assert len(model.calls) == 2
    assert outcome.answer == "Lotus [i1]." and [i.id for i in outcome.items] == ["i1"]


def test_citations_outside_item_ids_are_stripped() -> None:
    outcome, _ = _turn([submit("Lotus [i1], Bamboo [i2], Ghost [i7].", ["i1"])])
    assert outcome.answer == "Lotus [i1], Bamboo, Ghost."


def test_empty_text_is_an_error() -> None:
    outcome, _ = _turn([submit("  ", []), submit("No match.", [], call_id="submit_2")])
    assert _tool_messages(outcome)[0].status == "error"
    assert outcome.answer == "No match." and outcome.items == []


def test_exactly_one_reminder_then_fallback() -> None:
    outcome, model = _turn([ai("Lotus [i1] is good."), ai("Lotus [i1] and Ghost [i9].")])
    assert len(model.calls) == 2  # the reply, then one reminded call
    reminders = [m for m in outcome.state["messages"] if isinstance(m, HumanMessage)]
    assert [m.content for m in reminders][-1] == REMINDER
    assert outcome.answer_source == "fallback"
    assert outcome.answer == "Lotus [i1] and Ghost."  # invalid id stripped
    assert [i.id for i in outcome.items] == ["i1"]


def test_fallback_skips_an_empty_closing_reply() -> None:
    # Seen on gemma4:12b: a plain-text answer, the reminder, then a tool call and
    # an empty message. The answer is the plain-text reply, not "".
    outcome, model = _turn(
        [ai("Lotus [i1] is good."), ai("", tool_calls=[tool_call("geo_describe_area")]), ai("")]
    )
    assert len(model.calls) == 3
    assert outcome.answer_source == "fallback"
    assert outcome.answer == "Lotus [i1] is good."
    assert [i.id for i in outcome.items] == ["i1"]


def test_fallback_never_reuses_an_earlier_turn() -> None:
    messages = [
        HumanMessage("turn 1"), ai("Turn one answer."),
        HumanMessage("turn 2"), ai(""), HumanMessage(REMINDER), ai(""),
    ]  # fmt: skip
    assert _last_ai_text(messages) == ""
    assert _last_ai_text([*messages[:3], ai("Two."), HumanMessage(REMINDER), ai("")]) == "Two."


def test_reminder_can_lead_to_a_submit() -> None:
    outcome, model = _turn([ai("Lotus."), submit("Lotus [i1].", ["i1"])])
    assert len(model.calls) == 2 and outcome.answer_source == "submitted"


def test_call_limit_falls_back() -> None:
    cfg = GeoConfig(_env_file=None, agent=AgentConfig(max_model_calls_per_turn=2))
    looping = ai("", tool_calls=submit("x", ["i9"]).tool_calls)  # keeps failing
    outcome, _ = _turn([looping], cfg=cfg)
    assert outcome.stopped_reason == "call_limit"
    assert outcome.answer_source == "fallback" and outcome.items == []


def test_build_items_skips_unknown_and_keeps_order() -> None:
    assert [i.id for i in build_items(["i2", "i9", "i1"], ITEMS)] == ["i2", "i1"]


def test_final_answer_prefers_the_submission() -> None:
    state = {"items": ITEMS, "answer": {"text": "T [i2]", "item_ids": ["i2"]}}
    text, items, source = final_answer(state, "ignored [i1]")
    assert (text, [i.id for i in items], source) == ("T [i2]", ["i2"], "submitted")


def test_prompt_names_submit_answer_with_named_arguments() -> None:
    assert "submit_answer with text=" in SYSTEM_PROMPT and "item_ids=[" in SYSTEM_PROMPT
