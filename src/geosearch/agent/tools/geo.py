"""The one core area tool for Stage 2.

Two invariants shape its signature:
  - The model never chooses the area (invariant 2): the tool takes NO
    area argument. It reads `area_id` from state, which the app set.
  - The model never sees WKT (invariant 1): the tool returns a short text of
    plain numbers — size, bounds, a representative point — never geometry.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from geosearch.agent.context import AgentContext
from geosearch.agent.state import GeoAgentState


@tool
def geo_describe_area(runtime: ToolRuntime[AgentContext, GeoAgentState]) -> str:
    """Describe the conversation's fixed area: its size in km², its bounding box,
    and a representative point inside it. Call this for any question about the
    area's size, extent or location. Takes no arguments."""
    area_id = runtime.state["conversation"]["area_id"]
    ops = runtime.context.area_ops

    area_km2 = ops.area_km2(area_id)
    min_lon, min_lat, max_lon, max_lat = ops.bbox(area_id)
    rep_lon, rep_lat = ops.representative_point(area_id)

    return (
        f"Area size: {area_km2:.2f} km².\n"
        f"Bounding box (lon/lat): "
        f"[{min_lon:.4f}, {min_lat:.4f}] to [{max_lon:.4f}, {max_lat:.4f}].\n"
        f"A representative point inside the area is "
        f"longitude {rep_lon:.4f}, latitude {rep_lat:.4f}."
    )
