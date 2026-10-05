"""geo_describe_area unit tests, driven by a hand-built ToolRuntime (no model,
no graph). The two things that must hold: the model-visible schema has no
arguments, and the result never contains WKT."""

import re

import pytest
import shapely
from conftest import HANDOFF_POLYGON
from langchain.tools import ToolRuntime
from langchain_core.utils.function_calling import convert_to_openai_tool

from geosearch.agent.context import AgentContext
from geosearch.agent.tools.geo import geo_describe_area
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps


def _runtime(state: dict, context: AgentContext) -> ToolRuntime:
    """A minimal ToolRuntime: only state and context matter for this tool."""
    return ToolRuntime(
        state=state,
        context=context,
        config={},
        stream_writer=lambda *_: None,
        tool_call_id=None,
        store=None,
    )


def _setup() -> tuple[str, AgentContext]:
    cfg = GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    return area_id, AgentContext(area_ops=ops, cfg=cfg)


def test_schema_has_no_model_visible_arguments() -> None:
    assert geo_describe_area.args == {}
    params = convert_to_openai_tool(geo_describe_area)["function"]["parameters"]
    assert params["properties"] == {}


def _km2(result: str) -> float:
    match = re.search(r"([\d.]+)\s*km²", result)
    assert match, f"no km² figure in {result!r}"
    return float(match.group(1))


def test_describes_area_with_size_and_bounds() -> None:
    area_id, context = _setup()
    state = {"conversation": {"area_id": area_id}}
    result = geo_describe_area.func(runtime=_runtime(state, context))

    assert "km²" in result
    # HANDOFF_POLYGON is ~26.2 km²; the reported number must match it.
    assert _km2(result) == pytest.approx(26.2, rel=0.05)
    assert "representative point" in result.lower()


def test_result_never_contains_wkt() -> None:
    area_id, context = _setup()
    state = {"conversation": {"area_id": area_id}}
    result = geo_describe_area.func(runtime=_runtime(state, context))

    # WKT geometry literals carry a keyword immediately followed by "(".
    for literal in ("POLYGON(", "MULTIPOLYGON(", "POINT(", "POLYGON (", "POINT ("):
        assert literal not in result.upper()


def test_reads_area_id_from_state_not_an_argument() -> None:
    # Two different areas, same tool: the result follows state, proving the
    # tool takes the area from state rather than any caller-supplied argument.
    cfg = GeoConfig(_env_file=None)
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    context = AgentContext(area_ops=ops, cfg=cfg)

    small = store.put(shapely.from_wkt("POLYGON((0 0, 0.01 0, 0.01 0.01, 0 0.01, 0 0))"))
    big = store.put(shapely.from_wkt(HANDOFF_POLYGON))

    r_small = geo_describe_area.func(
        runtime=_runtime({"conversation": {"area_id": small}}, context)
    )
    r_big = geo_describe_area.func(runtime=_runtime({"conversation": {"area_id": big}}, context))
    assert r_small != r_big
    assert _km2(r_big) == pytest.approx(26.2, rel=0.05)
