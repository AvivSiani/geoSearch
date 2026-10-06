"""Stage 5 step 4: the places handlers (9101 search, 9102 details) through the
real resolver, against the replayed fixtures. No network, no model."""

import uuid
from pathlib import Path
from typing import Any

import pytest
import shapely
import yaml
from conftest import HANDOFF_POLYGON
from langchain.tools import ToolRuntime

from geosearch import sources
from geosearch.agent.context import AgentContext
from geosearch.agent.state import merge_items
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps
from geosearch.registry.handlers import handlers
from geosearch.registry.models import ToolDefinition
from geosearch.registry.resolver import check_output, resolve
from geosearch.sources.places import PLACES_DETAILS, PLACES_SEARCH

SEED = Path("registry/seeds/places.yaml")


@pytest.fixture
def env() -> tuple[AgentContext, dict[str, Any]]:
    sources.load_all()
    cfg = GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=10)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    state: dict[str, Any] = {
        "conversation": {"conversation_id": uuid.uuid4().hex, "area_id": area_id, "turn": 1},
        "request": {"request_id": "r", "prompt": "p", "language": "en"},
        "items": {},
        "files": {},
    }
    return AgentContext(area_ops=AreaOps(store), cfg=cfg), state


def _run(source_id: int, env: tuple, **args: Any) -> Any:
    context, state = env
    tool = resolve(ToolDefinition(source_id=source_id, description="d"))
    runtime = ToolRuntime(
        state=state, context=context, config={}, stream_writer=lambda *_: None,
        tool_call_id="c1", store=None,
    )  # fmt: skip
    out = tool.func(runtime=runtime, **args)
    if hasattr(out, "update"):
        state["items"] = merge_items(state["items"], out.update.get("items"))
    return out


def test_search_returns_items_inside_the_area_only(env: tuple) -> None:
    out = _run(PLACES_SEARCH, env, query="asian restaurant")
    message = out.update["messages"][0]
    assert message.content.startswith("Found 13 place(s) for 'asian restaurant'.")
    assert "3 result(s) outside the area were removed" in message.content
    rows = message.artifact["rows"]
    assert len(rows) == 10 and all(r["id"].startswith("i") for r in rows)
    context, state = env
    area_id = state["conversation"]["area_id"]
    for record in state["items"].values():
        assert context.area_ops.contains(area_id, record["row"]["lon"], record["row"]["lat"])
        assert record["ref"].startswith("ChIJ")
    assert "ChIJ" not in message.content + str(rows)


def test_search_filters_reach_the_provider(env: tuple) -> None:
    out = _run(PLACES_SEARCH, env, query="asian restaurant", max_price="inexpensive")
    rows = out.update["messages"][0].artifact["rows"]
    assert rows and all(r["price_level"] == "inexpensive" for r in rows)


def test_search_in_hebrew(env: tuple) -> None:
    env[1]["request"]["language"] = "he"
    out = _run(PLACES_SEARCH, env, query="מסעדה אסייתית")
    names = [r["name"] for r in out.update["messages"][0].artifact["rows"]]
    assert "לוטוס נודל בר" in names


def test_search_miss_is_empty_not_an_error(env: tuple) -> None:
    out = _run(PLACES_SEARCH, env, query="car wash")
    message = out.update["messages"][0]
    assert message.content == "Found 0 place(s) for 'car wash'."
    assert message.artifact is None and "items" not in out.update


def test_details_by_item_id_merge_into_the_item(env: tuple) -> None:
    _run(PLACES_SEARCH, env, query="asian restaurant")
    out = _run(PLACES_DETAILS, env, item_id="i1")
    message = out.update["messages"][0]
    assert message.content == "Details for i1."
    (row,) = message.artifact["rows"]
    assert row["id"] == "i1" and row["phone"] and row["opening_hours"]
    item = env[1]["items"]["i1"]
    assert item["source_id"] == PLACES_SEARCH  # first finder kept
    assert item["row"]["website"] and item["row"]["rating_count"]  # both tools' fields


def test_details_unknown_item(env: tuple) -> None:
    out = _run(PLACES_DETAILS, env, item_id="i42")
    message = out.update["messages"][0]
    assert "Unknown item id 'i42'" in message.content and message.artifact is None


def test_handlers_declare_items_and_coordinates(env: tuple) -> None:
    for source_id in (PLACES_SEARCH, PLACES_DETAILS):
        spec = handlers.get(source_id)
        assert spec.has_coordinates and spec.yields_items and spec.uses_area


def test_rows_pass_the_output_check(env: tuple) -> None:
    """Rows built from full and sparse provider places alike have exactly the
    declared fields (a None-filled sparse place must not fail the check)."""
    from geosearch.providers.places import ProviderPlace
    from geosearch.registry.handlers import ToolResult
    from geosearch.sources.places import PlaceDetailsRow, PlaceRow, _row

    sparse = ProviderPlace(id="x", name="n", lon=34.77, lat=32.07)
    for source_id, model in ((PLACES_SEARCH, PlaceRow), (PLACES_DETAILS, PlaceDetailsRow)):
        result = ToolResult(summary="s", artifact=[_row(sparse, model)])
        assert check_output(handlers.get(source_id), result) is None


def test_seed_entries_are_short_and_have_handlers() -> None:
    sources.load_all()
    tools = yaml.safe_load(SEED.read_text())["tools"]
    assert [t["source_id"] for t in tools] == [PLACES_SEARCH, PLACES_DETAILS]
    for t in tools:
        assert len(t["description"]) <= 200
        assert t["source_id"] in handlers
