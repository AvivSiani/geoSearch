"""Stage 5 gate, scripted (no model, no network): the handoff prompt, in English
and Hebrew, end to end through RequestRunner — real catalog with the places
tools, real handlers on replayed fixtures, real resolver, summarizer middleware
and submit_answer. The main agent and the summarizer are scripted apart, so we
can check exactly what each one saw.

Gate: 3–5 grounded items inside the polygon, in EN and HE, and the main agent
never sees raw rows.
"""

from dataclasses import dataclass

import pytest
from conftest import HANDOFF_POLYGON
from fake_registry import places_catalog
from langchain_core.messages import BaseMessage, SystemMessage
from scripted_model import ScriptedChatModel, ai, submit, tool_call

from geosearch.agent.context import AgentContext
from geosearch.agent.conversations import ConversationRegistry, make_checkpointer
from geosearch.agent.holder import AgentHolder
from geosearch.agent.ledger import TokenLedger
from geosearch.agent.run import RequestRunner, TurnOutcome
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.request.models import UserRequest

EN = "Find me a good Asian restaurant that is not too expensive."
HE = "מצא לי מסעדה אסייתית טובה שלא יקרה מדי."


@dataclass
class Case:
    prompt: str
    query: str
    summary: str
    answer: str
    names: tuple[str, ...]  # expected item names, in order


CASES = {
    "en": Case(
        EN, "asian restaurant",
        "Lotus Noodle Bar [i1] (4.6, moderate), Saigon Corner [i3] (4.5, cheap), "
        "Seoul Kitchen [i6] (4.5, moderate). Ghost [i42].",
        "Good, affordable picks: Lotus Noodle Bar [i1], Saigon Corner [i3] and Seoul Kitchen [i6].",
        ("Lotus Noodle Bar", "Saigon Corner", "Seoul Kitchen"),
    ),
    "he": Case(
        HE, "מסעדה אסייתית",
        "לוטוס נודל בר [i1] (4.6, מחיר בינוני), פינת סייגון [i3] (4.5, זול), "
        "המטבח של סיאול [i6] (4.5).",
        "המלצות טובות ולא יקרות: לוטוס נודל בר [i1], פינת סייגון [i3] והמטבח של סיאול [i6].",
        ("לוטוס נודל בר", "פינת סייגון", "המטבח של סיאול"),
    ),
}


@dataclass
class Run:
    outcome: TurnOutcome
    main: ScriptedChatModel
    summarizer: ScriptedChatModel
    ops: AreaOps
    ledger: TokenLedger


def _run(case: Case) -> Run:
    cfg = GeoConfig(_env_file=None)
    main = ScriptedChatModel(
        responses=[
            ai("", tool_calls=[tool_call("load_tools", {"source_ids": [9101, 9102]}, "l1")]),
            ai("", tool_calls=[tool_call(
                "source_9101", {"query": case.query, "max_price": "moderate"}, "s1")]),
            ai("", tool_calls=[tool_call("source_9102", {"item_id": "i1"}, "d1")]),
            submit(case.answer, ["i1", "i3", "i6"]),
        ]
    )  # fmt: skip
    summarizer = ScriptedChatModel(
        responses=[ai(case.summary), ai("[i1] open daily 12:00-23:00, phone listed.")]
    )
    store = InMemoryAreaStore(max_entries=10)
    ops = AreaOps(store)
    checkpointer = make_checkpointer(cfg)
    agents = AgentHolder(cfg, main, checkpointer, places_catalog(), summarizer)
    ledger = TokenLedger()

    class _Runner(RequestRunner):
        def _context(self) -> AgentContext:
            return AgentContext(area_ops=self.ops, cfg=self.cfg, ledger=ledger)

    runner = _Runner(
        cfg, agents, ConversationRegistry(cfg.conversation, checkpointer), store, ops,
        STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer),
    )  # fmt: skip
    outcome = runner.handle(UserRequest(wkt=HANDOFF_POLYGON, prompt=case.prompt))
    return Run(outcome, main, summarizer, ops, ledger)


def _text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in messages)


@pytest.fixture(params=["en", "he"])
def run(request: pytest.FixtureRequest) -> tuple[Case, Run]:
    case = CASES[request.param]
    return case, _run(case)


def test_three_to_five_grounded_items_inside_the_polygon(run: tuple[Case, Run]) -> None:
    case, r = run
    outcome = r.outcome
    assert outcome.answer_source == "submitted" and outcome.stopped_reason == "finished"
    assert outcome.answer == case.answer
    assert 3 <= len(outcome.items) <= 5
    assert tuple(i.name for i in outcome.items) == case.names
    area_id = outcome.state["conversation"]["area_id"]
    for item in outcome.items:
        assert item.id in outcome.state["items"]  # grounded: a tool returned it
        assert r.ops.contains(area_id, item.lon, item.lat)
        assert item.data["price_level"] in {"inexpensive", "moderate"}
    lotus = outcome.items[0]
    assert lotus.data["phone"] and lotus.data["rating_count"]  # details merged in


def test_main_agent_never_sees_raw_rows(run: tuple[Case, Run]) -> None:
    _, r = run
    main_seen = _text([m for call in r.main.calls for m in call])
    summarizer_seen = _text([m for call in r.summarizer.calls for m in call])
    # The rows went to the summarizer...
    assert "address=" in summarizer_seen and "rating_count=" in summarizer_seen
    # ...and never to the main agent: no row rendering, no provider id, no
    # address, no path to the raw file.
    for raw in ("address=", "rating_count=", "ChIJ", "/turns/", "Dizengoff", "דיזנגוף"):
        assert raw not in main_seen
    assert "ChIJ" not in summarizer_seen  # the provider id reaches no model at all
    assert "[i42]" not in main_seen  # an invented citation was stripped in code


def test_area_filter_and_language(run: tuple[Case, Run]) -> None:
    case, r = run
    state = r.outcome.state
    assert state["request"]["language"] == ("he" if case is CASES["he"] else "en")
    # 13 fixture rows; the price filter leaves 10, the area filter drops 3 outside.
    assert len(state["items"]) == 7
    note = "(3 result(s) outside the area were removed.)"
    assert any(note in str(m.content) for call in r.main.calls for m in call)
    system = r.summarizer.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert ("Hebrew" if case is CASES["he"] else "English") in system.content


def test_ledger_keeps_summarizer_calls_apart(run: tuple[Case, Run]) -> None:
    _, r = run
    usage = r.outcome.usage
    assert usage.model_calls == 4 and usage.summarizer_calls == 2
    assert not usage.over_budget
    registry_schemas = [rec.est_registry_tool_tokens for rec in r.ledger.records]
    assert registry_schemas[0] == 0 < registry_schemas[1]  # disclosure still holds
