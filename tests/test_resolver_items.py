"""Stage 5 step 3: the resolver's area filter (every tool) and items (short ids
for provider ids, merged across tools and turns)."""

import uuid
from typing import Any

import pytest
import shapely
from conftest import HANDOFF_POLYGON
from langchain.tools import ToolRuntime
from pydantic import BaseModel

from geosearch.agent.context import AgentContext
from geosearch.agent.state import merge_items
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps
from geosearch.registry.handlers import (
    HandlerContext,
    HandlerRegistry,
    InvalidHandler,
    ToolResult,
)
from geosearch.registry.models import ToolDefinition
from geosearch.registry.resolver import resolve

INSIDE = (34.77, 32.07)
OUTSIDE = (34.85, 32.07)  # east of the handoff polygon


class NoInput(BaseModel):
    pass


class ItemInput(BaseModel):
    item_id: str


class PlaceRow(BaseModel):
    id: str
    name: str
    lon: float
    lat: float
    phone: str | None = None


class PointRow(BaseModel):
    lon: float
    lat: float


class NameRow(BaseModel):
    name: str


def _place(ref: str, where: tuple[float, float], **extra: Any) -> dict[str, Any]:
    return {"id": ref, "name": f"n-{ref[-1]}", "lon": where[0], "lat": where[1], "phone": None} | extra


SEEN: dict[str, Any] = {}


def _registry(rows: list[dict] | None = None, data: dict | None = None) -> HandlerRegistry:
    reg = HandlerRegistry()

    @reg.register(source_id=1, input_model=NoInput, output_model=PlaceRow, uses_area=True)
    def search(args: NoInput, ctx: HandlerContext) -> ToolResult:
        return ToolResult(summary=f"Found {len(rows or [])}.", artifact=rows)

    @reg.register(source_id=2, input_model=ItemInput, output_model=PlaceRow, uses_area=True)
    def details(args: ItemInput, ctx: HandlerContext) -> ToolResult:
        SEEN["ref"] = ctx.item_ref(args.item_id)
        return ToolResult(summary="Details.", data=data or {})

    # uses_area=False, yet its rows are still filtered (lon/lat declared).
    @reg.register(source_id=3, input_model=NoInput, output_model=PointRow)
    def points(args: NoInput, ctx: HandlerContext) -> ToolResult:
        pts = [{"lon": INSIDE[0], "lat": INSIDE[1]}, {"lon": OUTSIDE[0], "lat": OUTSIDE[1]}]
        return ToolResult(summary="2 points.", artifact=pts)

    @reg.register(source_id=4, input_model=NoInput, output_model=NameRow)
    def names(args: NoInput, ctx: HandlerContext) -> ToolResult:
        return ToolResult(summary="Names.", artifact=[{"name": "a"}, {"name": "b"}])

    return reg


@pytest.fixture
def env() -> tuple[AgentContext, dict[str, Any]]:
    cfg = GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=10)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    state: dict[str, Any] = {
        "conversation": {"conversation_id": uuid.uuid4().hex, "area_id": area_id, "turn": 1},
        "items": {},
        "files": {},
    }
    return AgentContext(area_ops=AreaOps(store), cfg=cfg), state


def _run(reg: HandlerRegistry, source_id: int, env: tuple, **args: Any) -> Any:
    context, state = env
    tool = resolve(ToolDefinition(source_id=source_id, description="d"), reg)
    runtime = ToolRuntime(
        state=state, context=context, config={}, stream_writer=lambda *_: None,
        tool_call_id="c1", store=None,
    )  # fmt: skip
    return tool.func(runtime=runtime, **args)


def _apply(env: tuple, out: Any) -> None:
    """Fold a Command's state update in, as the graph's reducers would."""
    _, state = env
    state["items"] = merge_items(state["items"], out.update.get("items"))
    state["files"] = {**state["files"], **out.update.get("files", {})}


# --- area filter ------------------------------------------------------------------


def test_rows_outside_the_area_are_dropped_and_counted(env: tuple) -> None:
    rows = [_place("A", INSIDE), _place("B", OUTSIDE), _place("C", INSIDE)]
    out = _run(_registry(rows), 1, env)
    message = out.update["messages"][0]
    assert "1 result(s) outside the area were removed" in message.content
    assert [r["name"] for r in message.artifact["rows"]] == ["n-A", "n-C"]
    assert {rec["ref"] for rec in out.update["items"].values()} == {"A", "C"}


def test_filter_applies_to_tools_without_uses_area(env: tuple) -> None:
    out = _run(_registry(), 3, env)
    message = out.update["messages"][0]
    assert message.artifact["rows"] == [{"lon": INSIDE[0], "lat": INSIDE[1]}]
    assert "1 result(s) outside" in message.content


def test_single_row_data_outside_is_dropped(env: tuple) -> None:
    out = _run(_registry(data=_place("Z", OUTSIDE)), 2, env, item_id="i1")
    message = out.update["messages"][0]
    assert message.artifact is None and "items" not in out.update
    assert "1 result(s) outside" in message.content


def test_tools_without_coordinates_are_untouched(env: tuple) -> None:
    out = _run(_registry(), 4, env)
    message = out.update["messages"][0]
    assert message.content == "Names."
    assert message.artifact["rows"] == [{"name": "a"}, {"name": "b"}]


# --- items ------------------------------------------------------------------------


def test_rows_become_items_with_short_ids(env: tuple) -> None:
    out = _run(_registry([_place("ChIJ-A", INSIDE), _place("ChIJ-B", INSIDE)]), 1, env)
    message = out.update["messages"][0]
    rows = message.artifact["rows"]
    assert [r["id"] for r in rows] == ["i1", "i2"]
    assert rows[0] == {"id": "i1", "name": "n-A"}  # no coords, no Nones
    # The provider id never reaches the model: not in content, not in the summarizer rows.
    assert "ChIJ" not in message.content and "ChIJ" not in str(rows)
    assert out.update["items"]["i1"] == {
        "source_id": 1, "ref": "ChIJ-A",
        "row": {"name": "n-A", "lon": INSIDE[0], "lat": INSIDE[1], "phone": None},
    }  # fmt: skip


def test_ids_are_stable_across_calls(env: tuple) -> None:
    reg = _registry([_place("A", INSIDE), _place("B", INSIDE)])
    _apply(env, _run(reg, 1, env))
    reg2 = _registry([_place("B", INSIDE), _place("C", INSIDE)])
    out = _run(reg2, 1, env)
    assert [r["id"] for r in out.update["messages"][0].artifact["rows"]] == ["i2", "i3"]


def test_parallel_calls_never_collide(env: tuple) -> None:
    # Same (stale) state for both: in-process memory keeps ids unique and shared.
    a = _run(_registry([_place("A", INSIDE)]), 1, env)
    b = _run(_registry([_place("B", INSIDE), _place("A", INSIDE)]), 1, env)
    assert list(a.update["items"]) == ["i1"]
    assert list(b.update["items"]) == ["i2", "i1"]


def test_details_resolve_the_short_id_and_merge_into_the_item(env: tuple) -> None:
    _apply(env, _run(_registry([_place("A", INSIDE)]), 1, env))
    out = _run(_registry(data=_place("A", INSIDE, phone="03-1")), 2, env, item_id="i1")
    assert SEEN["ref"] == "A"
    message = out.update["messages"][0]
    assert message.content == "Details."  # the row is never inline: it holds the ref
    assert message.artifact["rows"] == [{"id": "i1", "name": "n-A", "phone": "03-1"}]
    _apply(env, out)
    item = env[1]["items"]["i1"]
    assert item["source_id"] == 1 and item["row"]["phone"] == "03-1"


def test_unknown_item_id_resolves_to_none(env: tuple) -> None:
    _run(_registry(data=_place("A", INSIDE)), 2, env, item_id="i99")
    assert SEEN["ref"] is None


def test_merge_items_never_blanks_a_field() -> None:
    left = {"i1": {"source_id": 1, "ref": "A", "row": {"name": "x", "phone": "1"}}}
    right = {"i1": {"source_id": 2, "ref": "A", "row": {"name": "y", "phone": None}}}
    merged = merge_items(left, right)
    assert merged["i1"] == {"source_id": 1, "ref": "A", "row": {"name": "y", "phone": "1"}}
    assert merge_items(None, None) == {}


def test_id_field_must_be_a_string() -> None:
    class BadRow(BaseModel):
        id: int

    with pytest.raises(InvalidHandler, match="'id'"):
        HandlerRegistry().register(source_id=5, input_model=NoInput, output_model=BadRow)
