"""Stage 5 step 5: citations, the summarizer (map-reduce) and its middleware.
Scripted models only."""

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command
from scripted_model import ScriptedChatModel, ai

from geosearch.agent.citations import cited_ids, strip_invalid_ids
from geosearch.agent.ledger import TokenLedger
from geosearch.agent.summarizer import (
    ResultSummarizer,
    SummarizerMiddleware,
    chunk_lines,
    render_row,
)
from geosearch.config import GeoConfig, SummarizerConfig
from geosearch.registry.catalog import CatalogEntry, CatalogSnapshot

ROWS = [{"id": f"i{n}", "name": f"Place {n}", "rating": 4.0 + n / 10} for n in range(1, 4)]


# --- citations --------------------------------------------------------------------


def test_cited_ids_in_order_without_repeats() -> None:
    assert cited_ids("A [i2], B [i1, i3] and A again [i2].") == ["i2", "i1", "i3"]
    assert cited_ids("no citations, [x1], [3]") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Lotus [i1] and Ghost [i9].", "Lotus [i1] and Ghost."),
        ("Both [i1, i9, i2].", "Both [i1][i2]."),
        ("None [i8, i9] here.", "None here."),
        ("[i9] leads.", " leads."),
        ("Keep [note] and [3].", "Keep [note] and [3]."),  # not item citations
    ],
)
def test_strip_invalid_ids(text: str, expected: str) -> None:
    assert strip_invalid_ids(text, {"i1", "i2"}) == expected


# --- rendering and chunking ---------------------------------------------------------


def test_render_row() -> None:
    assert render_row({"id": "i3", "name": "Lotus", "rating": 4.6}) == "[i3] name=Lotus; rating=4.6"
    assert render_row({"lon": 34.7, "lat": 32.0}) == "- lon=34.7; lat=32.0"


def test_chunk_lines_packs_in_order_and_counts_omitted() -> None:
    lines = ["x" * 400] * 5  # ~100 tokens each
    chunks, omitted = chunk_lines(lines, chunk_tokens=250, max_chunks=10)
    assert chunks == [[0, 1], [2, 3], [4]] and omitted == 0
    chunks, omitted = chunk_lines(lines, chunk_tokens=250, max_chunks=2)
    assert chunks == [[0, 1], [2, 3]] and omitted == 1
    # An oversized line still gets a chunk of its own.
    assert chunk_lines(["y" * 4_000], chunk_tokens=10, max_chunks=3) == ([[0]], 0)


# --- the summarizer ---------------------------------------------------------------


def _summarizer(responses: list, **sizes: int) -> tuple[ResultSummarizer, ScriptedChatModel]:
    cfg = GeoConfig(_env_file=None, summarizer=SummarizerConfig(**sizes))
    model = ScriptedChatModel(responses=responses)
    return ResultSummarizer(model, cfg), model


def _summarize(s: ResultSummarizer, rows: list = ROWS, **kw: Any) -> Any:
    args = {"question": "cheap asian food?", "language": "en", "tool": "Search places.",
            "headline": "Found 3.", "rows": rows} | kw  # fmt: skip
    return s.summarize(**args)


def test_one_chunk_is_one_call_with_isolated_context() -> None:
    s, model = _summarizer([ai("Best: Place 1 [i1], Ghost [i7].")])
    ledger = TokenLedger()
    out = _summarize(s, ledger=ledger)
    assert out.text == "Best: Place 1 [i1], Ghost." and out.calls == 1
    (messages,) = model.calls
    assert len(messages) == 2 and isinstance(messages[0], SystemMessage)  # no history
    human = messages[1].content
    assert "Question: cheap asian food?" in human and "[i2] name=Place 2; rating=4.2" in human
    assert "English" in messages[0].content
    assert len(ledger.summarizer_records) == 1 and ledger.records == []
    assert ledger.summarizer_records[0].role == "summarizer"
    usage = ledger.summary()
    assert usage.summarizer_calls == 1 and usage.model_calls == 0


def test_hebrew_is_requested() -> None:
    s, model = _summarizer([ai("סיכום [i1]")])
    assert _summarize(s, language="he").text == "סיכום [i1]"
    assert "Hebrew" in model.calls[0][0].content


def test_large_results_map_then_merge() -> None:
    rows = [{"id": f"i{n}", "name": "n" * 200} for n in range(1, 7)]  # ~55 tokens per line
    s, model = _summarizer(
        [ai("A [i1] [i9]"), ai("B [i3] [i1]"), ai("C [i5]"), ai("All: [i1] [i3] [i5] [i42]")],
        chunk_tokens=120,
    )
    out = _summarize(s, rows=rows)
    assert out.calls == 4 and len(model.calls) == 4  # 3 map + 1 merge
    assert out.text == "All: [i1] [i3] [i5]"  # invented id stripped at the merge
    merge_input = model.calls[3][1].content
    assert "A [i1]\n" in merge_input and "[i9]" not in merge_input  # map outputs checked too
    assert "B [i3]" in merge_input  # i1 belongs to chunk 1, so chunk 2 may not cite it


def test_rows_beyond_max_chunks_are_counted() -> None:
    rows = [{"id": f"i{n}", "name": "n" * 400} for n in range(1, 5)]
    s, _ = _summarizer([ai("x [i1]"), ai("y [i2]"), ai("merged [i1][i2]")],
                       chunk_tokens=110, max_chunks=2)  # fmt: skip
    out = _summarize(s, rows=rows)
    assert out.omitted == 2 and out.calls == 3


def test_output_is_capped() -> None:
    s, _ = _summarizer([ai("z" * 10_000)], max_output_tokens=50)
    assert len(_summarize(s).text) == 200


# --- the middleware ---------------------------------------------------------------


SNAPSHOT = CatalogSnapshot(tools=(CatalogEntry(9101, "source_9101", "Search places."),))


def _request(ledger: TokenLedger | None = None) -> Any:
    return SimpleNamespace(
        state={"request": {"prompt": "asian food?", "language": "en"}},
        runtime=SimpleNamespace(context=SimpleNamespace(ledger=ledger or TokenLedger())),
        tool_call={"name": "source_9101", "id": "c1"},
    )


def _tool_message(artifact: Any, content: str = "Found 3.") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="c1", name="source_9101", artifact=artifact)


def _middleware(responses: list) -> tuple[SummarizerMiddleware, ScriptedChatModel]:
    s, model = _summarizer(responses)
    return SummarizerMiddleware(s, SNAPSHOT), model


def test_middleware_replaces_rows_with_the_summary() -> None:
    mw, model = _middleware([ai("Place 1 [i1] is best.")])
    files = {"/f.json": {}}
    command = Command(update={"messages": [_tool_message({"source_id": 9101, "rows": ROWS})],
                              "files": files, "items": {"i1": {}}})  # fmt: skip
    out = mw.wrap_tool_call(_request(), lambda r: command)
    message = out.update["messages"][0]
    assert message.content == "Found 3.\nPlace 1 [i1] is best."
    assert message.artifact is None  # the rows never reach the checkpointed history
    assert message.tool_call_id == "c1" and message.name == "source_9101"
    assert out.update["files"] is files and out.update["items"] == {"i1": {}}
    assert "Tool: Search places." in model.calls[0][1].content  # the catalog description


def test_results_without_rows_pass_through_without_a_call() -> None:
    mw, model = _middleware([ai("never")])
    plain = ToolMessage(content='One.\ndata: {"aqi":42}', tool_call_id="c1", name="source_105")
    assert mw.wrap_tool_call(_request(), lambda r: plain) is plain
    error = ToolMessage(content="Error: x", tool_call_id="c1", status="error")
    assert mw.wrap_tool_call(_request(), lambda r: error) is error
    empty = _tool_message({"source_id": 9101, "rows": []}, content="Found 0.")
    out = mw.wrap_tool_call(_request(), lambda r: empty)
    assert out.content == "Found 0." and out.artifact is None
    assert model.calls == []


def test_summarizer_failure_keeps_ids_and_names_only() -> None:
    class Broken(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("model down")

    cfg = GeoConfig(_env_file=None)
    mw = SummarizerMiddleware(ResultSummarizer(Broken(responses=[AIMessage("")]), cfg), SNAPSHOT)
    out = mw.wrap_tool_call(_request(), lambda r: _tool_message({"source_id": 9101, "rows": ROWS}))
    assert out.content.startswith("Found 3.\nSummary unavailable. Items: [i1] Place 1;")
    assert "rating" not in out.content and out.artifact is None
