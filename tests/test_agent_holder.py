"""Stage 4 step 5: AgentHolder rebuilds the agent on a registry change, only
between requests, and in-flight requests keep their agent."""

from fake_registry import DEMO_DEFINITION, scale_catalog, scale_registry
from langgraph.checkpoint.memory import InMemorySaver
from scripted_model import ScriptedChatModel, ai

from geosearch.agent.holder import AgentHolder
from geosearch.config import GeoConfig
from geosearch.registry.models import ToolDefinition


def _holder(registry=None):
    registry = registry or scale_registry(include=[101])
    catalog = scale_catalog(registry)
    model = ScriptedChatModel(responses=[ai("ok")])
    return AgentHolder(GeoConfig(_env_file=None), model, InMemorySaver(), catalog), registry


def _tool_names(agent) -> set[str]:
    return set(agent.nodes["tools"].bound.tools_by_name)


def test_agent_registers_core_and_every_catalog_tool() -> None:
    holder, _ = _holder(scale_registry())
    names = _tool_names(holder.current())
    assert {"geo_describe_area", "load_tools"} <= names
    assert {f"source_{i}" for i in [*range(101, 111), 9001]} <= names


def test_no_change_means_the_same_agent() -> None:
    holder, _ = _holder()
    assert holder.current() is holder.current()


def test_registry_change_rebuilds_on_the_next_request() -> None:
    holder, registry = _holder()
    first = holder.current()
    assert "source_9001" not in _tool_names(first)

    registry.upsert_tool(DEMO_DEFINITION)
    second = holder.current()
    assert second is not first
    assert "source_9001" in _tool_names(second)
    assert holder.snapshot.revision == registry.revision()


def test_in_flight_request_keeps_its_agent() -> None:
    holder, registry = _holder()
    in_flight = holder.current()
    registry.upsert_tool(ToolDefinition(source_id=102, description="Forecast."))
    holder.current()  # another request triggers the rebuild
    assert "source_102" not in _tool_names(in_flight)  # unchanged under the old request


def test_registry_outage_keeps_the_current_agent() -> None:
    holder, registry = _holder()
    agent = holder.current()
    registry.down = True
    registry.upsert_tool(DEMO_DEFINITION)
    assert holder.current() is agent


def test_holder_without_catalog_has_core_tools_only() -> None:
    holder = AgentHolder(
        GeoConfig(_env_file=None), ScriptedChatModel(responses=[ai("ok")]), InMemorySaver()
    )
    names = _tool_names(holder.current())
    assert not any(n.startswith("source_") for n in names)
    assert "load_tools" in names
