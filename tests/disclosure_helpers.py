"""Helpers for driving disclosure middleware directly (no graph, no model)."""

from types import SimpleNamespace
from typing import Any

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, SystemMessage

from geosearch.agent.context import AgentContext
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps


def context(cfg: GeoConfig | None = None) -> AgentContext:
    cfg = cfg or GeoConfig(_env_file=None)
    return AgentContext(area_ops=AreaOps(InMemoryAreaStore(max_entries=10)), cfg=cfg)


def model_request(
    *,
    state: dict[str, Any] | None = None,
    tools: list[Any] | None = None,
    system: str | None = "Base prompt.",
    ctx: AgentContext | None = None,
) -> ModelRequest:
    return ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[],
        system_message=SystemMessage(system) if system is not None else None,
        tools=tools or [],
        state=state or {},
        runtime=SimpleNamespace(context=ctx or context()),  # type: ignore[arg-type]
    )


class Capture:
    """A `handler` that records the request it was given."""

    def __init__(self) -> None:
        self.request: ModelRequest | None = None

    def __call__(self, request: ModelRequest) -> Any:
        self.request = request
        return SimpleNamespace(result=[AIMessage("ok")])
