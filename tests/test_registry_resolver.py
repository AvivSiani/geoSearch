"""Stage 3 step 6: the resolver. No MongoDB: definitions are built in code and
tools run either directly (hand-built ToolRuntime) or through a real ToolNode."""

import json
import uuid
from enum import StrEnum
from typing import Annotated, Any, TypedDict

import pytest
import shapely
from conftest import HANDOFF_POLYGON
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
from pydantic import BaseModel, Field

from geosearch import sources
from geosearch.agent.context import AgentContext
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps
from geosearch.registry.handlers import HandlerContext, HandlerRegistry, ToolResult, UnknownHandler
from geosearch.registry.models import ToolDefinition
from geosearch.registry.resolver import TRUNCATED, resolve


class Kind(StrEnum):
    cafe = "cafe"
    park = "park"


class FindInput(BaseModel):
    kind: Kind = Field(description="What to find.")
    limit: int = Field(default=5, ge=1, le=20, description="Max results.")
    tags: list[str] = Field(default_factory=list)
    near: str | None = None


class Row(BaseModel):
    name: str
    score: float


SEEN: dict[str, Any] = {}


def _make_handlers(result: Any = None, raises: Exception | None = None) -> HandlerRegistry:
    reg = HandlerRegistry()

    @reg.register(source_id=17, input_model=FindInput, output_model=Row, uses_area=True)
    def find(args: FindInput, ctx: HandlerContext) -> ToolResult:
        SEEN["args"], SEEN["ctx"] = args, ctx
        if raises:
            raise raises
        return result

    @reg.register(source_id=18, input_model=FindInput, output_model=Row)
    def noarea(args: FindInput, ctx: HandlerContext) -> ToolResult:
        SEEN["ctx"] = ctx
        return ToolResult(summary="ok", data={"name": "a", "score": 1.0})

    return reg


TD = ToolDefinition(source_id=17, description="Find things in the area.")


@pytest.fixture
def setup() -> tuple[AgentContext, dict[str, Any]]:
    cfg = GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=10)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    state = {
        "conversation": {
            "conversation_id": uuid.uuid4().hex,  # file numbering is per conversation
            "area_id": area_id,
            "turn": 2,
            "area_wkt": HANDOFF_POLYGON,
        }
    }
    return AgentContext(area_ops=AreaOps(store), cfg=cfg), state


def _runtime(state: dict, context: AgentContext, call_id: str = "call_1") -> ToolRuntime:
    return ToolRuntime(
        state=state, context=context, config={}, stream_writer=lambda *_: None,
        tool_call_id=call_id, store=None,
    )  # fmt: skip


def _call(tool: Any, setup: tuple, **args: Any) -> Command | ToolMessage:
    context, state = setup
    return tool.func(runtime=_runtime(state, context), **args)


def _content(out: Command | ToolMessage) -> str:
    if isinstance(out, ToolMessage):
        return out.content
    return out.update["messages"][0].content


# --- schema -----------------------------------------------------------------------


def test_model_facing_schema() -> None:
    tool = resolve(TD, _make_handlers())
    fn = convert_to_openai_tool(tool)["function"]
    assert fn["name"] == "source_17"
    assert fn["description"] == "Find things in the area."
    params = fn["parameters"]
    assert set(params["properties"]) == {"kind", "limit", "tags", "near"}
    assert params["required"] == ["kind"]
    assert params["properties"]["kind"] == {
        "type": "string", "description": "What to find.", "enum": ["cafe", "park"],
    }  # fmt: skip
    assert params["properties"]["limit"] == {
        "type": "integer", "description": "Max results.", "default": 5, "minimum": 1, "maximum": 20,
    }  # fmt: skip
    text = json.dumps(params)
    for absent in ("title", "$ref", "$defs", "runtime", "area_id", "wkt", "geometry"):
        assert absent not in text


def test_unknown_handler_is_not_resolved() -> None:
    td = ToolDefinition(source_id=99, description="d")
    with pytest.raises(UnknownHandler, match="99"):
        resolve(td, _make_handlers())


# --- context ----------------------------------------------------------------------


def test_area_and_turn_come_from_state(setup: tuple) -> None:
    tool = resolve(TD, _make_handlers(ToolResult(summary="none found")))
    out = _call(tool, setup, kind="cafe")
    ctx: HandlerContext = SEEN["ctx"]
    assert ctx.area_id == setup[1]["conversation"]["area_id"]
    assert ctx.turn == 2 and ctx.source_id == 17
    assert ctx.area_ops is setup[0].area_ops and ctx.cfg is setup[0].cfg
    assert SEEN["args"] == FindInput(kind=Kind.cafe)
    assert _content(out) == "none found"
    assert ctx.language == "en"  # no request in state: the default


def test_language_comes_from_the_request(setup: tuple) -> None:
    context, state = setup
    state["request"] = {"request_id": "r", "prompt": "מסעדה", "language": "he"}
    _call(resolve(TD, _make_handlers(ToolResult(summary="x"))), setup, kind="cafe")
    assert SEEN["ctx"].language == "he"


def test_tools_without_uses_area_get_no_area(setup: tuple) -> None:
    td = ToolDefinition(source_id=18, description="d")
    _call(resolve(td, _make_handlers()), setup, kind="park")
    assert SEEN["ctx"].area_id is None


def test_through_a_real_toolnode(setup: tuple) -> None:
    """ToolNode injects the runtime (state, context, tool_call_id); the model's
    arguments never include the area, yet the handler gets the conversation's."""

    def merge(a: dict | None, b: dict | None) -> dict:
        return {**(a or {}), **(b or {})}

    class S(TypedDict):
        messages: Annotated[list, add_messages]
        files: Annotated[dict, merge]
        conversation: dict

    sources.load_all()
    tool = resolve(ToolDefinition(source_id=9001, description="Points."))
    graph = StateGraph(S, context_schema=AgentContext)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    context, state = setup

    call = {"name": "source_9001", "args": {"count": 4}, "id": "c9", "type": "tool_call"}
    out = graph.compile().invoke(
        {"messages": [AIMessage("", tool_calls=[call])], "files": {}, **state}, context=context
    )
    message = out["messages"][-1]
    assert message.tool_call_id == "c9"
    assert "full result: /turns/2/results/9001/1.json" in message.content
    rows = json.loads(out["files"]["/turns/2/results/9001/1.json"]["content"])
    assert len(rows) == 4
    area_id = state["conversation"]["area_id"]
    assert all(context.area_ops.contains(area_id, r["lon"], r["lat"]) for r in rows)
    assert "POLYGON" not in message.content


# --- arguments and errors ---------------------------------------------------------


@pytest.mark.parametrize("args", [{}, {"kind": "zoo"}, {"kind": "cafe", "limit": 99}])
def test_bad_arguments_become_a_short_error(setup: tuple, args: dict) -> None:
    SEEN.clear()
    out = _call(resolve(TD, _make_handlers()), setup, **args)
    assert isinstance(out, ToolMessage) and out.status == "error"
    assert out.content.startswith("Error: invalid arguments:")
    assert out.tool_call_id == "call_1" and len(out.content) < 300
    assert "args" not in SEEN  # the handler never ran


def test_handler_exception_becomes_a_short_error(setup: tuple) -> None:
    out = _call(
        resolve(TD, _make_handlers(raises=RuntimeError("secret internals"))), setup, kind="cafe"
    )
    assert isinstance(out, ToolMessage) and out.status == "error"
    assert out.content == "Error: source_17 failed (RuntimeError)"
    assert "Traceback" not in out.content and "secret" not in out.content


def test_missing_area_is_an_error(setup: tuple) -> None:
    context, _ = setup
    out = resolve(TD, _make_handlers(ToolResult(summary="x"))).func(
        runtime=_runtime({"conversation": {"turn": 1}}, context), kind="cafe"
    )
    assert isinstance(out, ToolMessage) and "no area" in out.content


# --- output-model check -----------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        ToolResult(summary="s", artifact=[{"name": "a", "score": 1.0}, {"name": "b"}]),  # missing
        ToolResult(summary="s", artifact=[{"name": "a", "score": 1.0, "secret": 1}]),  # extra
        ToolResult(summary="s", artifact=[{"name": "a", "score": "high"}]),  # wrong type
        ToolResult(summary="s", artifact=["not a row"]),
        ToolResult(summary="s", data={"name": "a"}),  # data checked when no list artifact
        "not a ToolResult",
    ],
)
def test_output_mismatch_becomes_an_error(
    setup: tuple, result: Any, caplog: pytest.LogCaptureFixture
) -> None:
    out = _call(resolve(TD, _make_handlers(result)), setup, kind="cafe")
    assert isinstance(out, ToolMessage) and out.status == "error"
    assert out.content == "Error: source_17 returned malformed output (name, score)"
    assert "does not match" in caplog.text


# --- result shaping ---------------------------------------------------------------


def test_artifact_goes_to_a_file_with_a_pointer(setup: tuple) -> None:
    rows = [{"name": f"n{i}", "score": float(i)} for i in range(3)]
    result = ToolResult(summary="Found 3.", data={"name": "n0", "score": 0.0}, artifact=rows)
    out = _call(resolve(TD, _make_handlers(result)), setup, kind="cafe")
    assert isinstance(out, Command)
    path = "/turns/2/results/17/1.json"
    assert _content(out) == f'Found 3.\ndata: {{"name":"n0","score":0.0}}\nfull result: {path}'
    file = out.update["files"][path]
    assert json.loads(file["content"]) == rows
    assert set(file) >= {"content", "encoding", "created_at", "modified_at"}


def test_next_free_k_skips_existing_files(setup: tuple) -> None:
    context, state = setup
    state = {**state, "files": {"/turns/2/results/17/1.json": {}, "/turns/2/results/17/2.json": {}}}
    result = ToolResult(summary="s", artifact=[{"name": "a", "score": 1.0}])
    out = resolve(TD, _make_handlers(result)).func(runtime=_runtime(state, context), kind="cafe")
    assert list(out.update["files"]) == ["/turns/2/results/17/3.json"]


def test_parallel_calls_in_one_step_get_distinct_files(setup: tuple) -> None:
    result = ToolResult(summary="s", artifact=[{"name": "a", "score": 1.0}])
    tool = resolve(TD, _make_handlers(result))
    context, state = setup
    state = {**state, "conversation": {**state["conversation"], "turn": 7}}
    a = tool.func(runtime=_runtime(state, context, "c1"), kind="cafe")
    b = tool.func(runtime=_runtime(state, context, "c2"), kind="cafe")  # same (stale) state
    assert list(a.update["files"]) != list(b.update["files"])


def test_small_results_stay_inline_without_files(setup: tuple) -> None:
    result = ToolResult(summary="One.", data={"name": "a", "score": 1.0})
    out = _call(resolve(TD, _make_handlers(result)), setup, kind="cafe")
    assert _content(out) == 'One.\ndata: {"name":"a","score":1.0}'
    assert "files" not in out.update


def test_oversized_data_is_offloaded_automatically(setup: tuple) -> None:
    result = ToolResult(summary="Big.", data={"name": "n" * 2_000, "score": 1.0})
    out = _call(resolve(TD, _make_handlers(result)), setup, kind="cafe")
    content = _content(out)
    assert content == "Big.\nfull result: /turns/2/results/17/1.json"
    stored = json.loads(out.update["files"]["/turns/2/results/17/1.json"]["content"])
    assert stored["name"] == "n" * 2_000


def test_long_summary_is_truncated_within_the_limit(setup: tuple) -> None:
    limit = setup[0].cfg.registry.max_inline_result_chars
    result = ToolResult(summary="s" * 5_000, artifact=[{"name": "a", "score": 1.0}])
    content = _content(_call(resolve(TD, _make_handlers(result)), setup, kind="cafe"))
    assert len(content) <= limit
    assert TRUNCATED + "\nfull result: /turns/2/results/17/1.json" in content


def test_data_too_big_alongside_artifact_gets_its_own_file(setup: tuple) -> None:
    limit = setup[0].cfg.registry.max_inline_result_chars
    result = ToolResult(
        summary="Both.",
        data={"name": "n" * 2_000, "score": 1.0},
        artifact=[{"name": "a", "score": 1.0}],
    )
    out = _call(resolve(TD, _make_handlers(result)), setup, kind="cafe")
    content = _content(out)
    assert len(content) <= limit
    assert content == (
        "Both.\ndata: /turns/2/results/17/2.json\nfull result: /turns/2/results/17/1.json"
    )
    assert len(out.update["files"]) == 2
