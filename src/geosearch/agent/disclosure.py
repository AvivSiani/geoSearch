"""Progressive disclosure of registry tools (Stage 4).

The agent sees an always-on catalog of `id: description` lines, decides which
tools the request needs, and loads them with `load_tools`. It pays for a tool's
schema only after loading it:

  - CatalogMiddleware appends the catalog block to the system message.
  - DisclosureMiddleware filters the offered tools to core tools plus loaded
    ones, and blocks calls to registry tools that aren't loaded.

Both are bound to one immutable CatalogSnapshot at agent build time, so a
request can never see a catalog that doesn't match its agent's registered tools.
"""

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from geosearch.registry.catalog import CatalogSnapshot

# Named-argument form on purpose: with the positional `load_tools([ids])` hint,
# gemma4 on Ollama 0.35.1 (thinking off) copies it, and a multi-id call such as
# `load_tools([9001, 101])` is dropped by Ollama's tool-call parser, leaving an
# empty reply. Verified with a captured request replayed against the server.
CATALOG_HEADER = (
    "Tools (call load_tools with source_ids=[id, ...] for every tool the request needs, "
    "then use them):"
)
NO_TOOLS = "No tools available."


def render_catalog(snapshot: CatalogSnapshot, loaded: list[int] | None) -> str:
    """Sorted by id, so the block is byte-stable between calls and a prompt
    cache (Ollama's) can reuse it; only `(loaded)` marks change."""
    if not snapshot.tools:
        return NO_TOOLS
    loaded_ids = set(loaded or [])
    lines = [CATALOG_HEADER]
    for entry in snapshot.tools:
        mark = " (loaded)" if entry.source_id in loaded_ids else ""
        lines.append(f"{entry.source_id}: {entry.description}{mark}")
    return "\n".join(lines)


class CatalogMiddleware(AgentMiddleware):
    """Appends the catalog block to the system message on every model call.
    Appends, never replaces, so the base prompt and area line survive."""

    def __init__(self, snapshot: CatalogSnapshot):
        super().__init__()
        self.snapshot = snapshot

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        block = render_catalog(self.snapshot, request.state.get("loaded_tools"))
        # Tell the ledger how big the catalog is (it flags catalog_warn_tokens).
        request.runtime.context.ledger.note_catalog(
            count_tokens_approximately([SystemMessage(block)])
        )
        base = request.system_prompt or ""
        text = f"{base}\n\n{block}" if base else block
        return handler(request.override(system_message=SystemMessage(text)))


class DisclosureMiddleware(AgentMiddleware):
    """Every catalog tool is registered with the agent (so loaded tools get
    native tool calling with real schemas), but a registry tool's schema is
    sent only while it is loaded, and it runs only while it is loaded
    (Stage 4 invariants 1-2). Core tools — anything not from the registry —
    always pass."""

    def __init__(self, snapshot: CatalogSnapshot):
        super().__init__()
        self.snapshot = snapshot
        self.registry_names = frozenset(entry.model_name for entry in snapshot.tools)

    def _loaded(self, state: Any) -> set[int]:
        return set((state or {}).get("loaded_tools") or [])

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        loaded = self._loaded(request.state)
        # A loaded id whose tool was later deleted has no tool here: simply ignored.
        visible = [
            t
            for t in request.tools
            if t.name not in self.registry_names or self.snapshot.source_id_of(t.name) in loaded
        ]
        request.runtime.context.ledger.note_registry_tools(self.registry_names)
        return handler(request.override(tools=visible))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        name = request.tool_call["name"]
        source_id = self.snapshot.source_id_of(name)
        if source_id is None or source_id in self._loaded(request.state):
            return handler(request)
        return ToolMessage(
            f"{name} is not loaded. Call load_tools([{source_id}]) first.",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )
