"""`load_tools`: the agent's one way to make registry tools usable.

The agent reads the catalog (`id: description`), decides which tools the request
needs, and loads them all in one call. Only then does it pay for their schemas
(DisclosureMiddleware offers loaded tools only). The result is where the agent
first learns what each tool returns — the catalog never shows returned fields
(Stage 4 invariant 3).

Each id is handled independently and reported on its own line; the state
change is the ids actually loaded. `loaded_tools` has a union reducer, so the
update lists only the new ids, and parallel calls both land.

Built per agent by `make_load_tools(snapshot)` rather than as a module-level
tool, so it validates against the same catalog snapshot the agent was built with.
"""

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.types import Command

from geosearch.agent.context import AgentContext
from geosearch.agent.state import GeoAgentState
from geosearch.registry.catalog import CatalogSnapshot


def make_load_tools(snapshot: CatalogSnapshot) -> BaseTool:
    descriptions = {entry.source_id: entry for entry in snapshot.tools}

    @tool
    def load_tools(
        source_ids: list[int], runtime: ToolRuntime[AgentContext, GeoAgentState]
    ) -> Command:
        """Load the tools the request needs, chosen by their catalog descriptions.
        Pass every needed id in one call, e.g. source_ids=[17, 9001]."""
        cap = runtime.context.cfg.disclosure.max_loaded_tools
        already = [i for i in runtime.state.get("loaded_tools") or [] if i in descriptions]

        new: list[int] = []
        lines: list[str] = []
        unknown: list[int] = []
        over_cap: list[int] = []
        for source_id in dict.fromkeys(source_ids):  # de-duplicate, keep order
            entry = descriptions.get(source_id)
            if entry is None:
                unknown.append(source_id)
            elif source_id in already:
                lines.append(f"- {source_id} already loaded")
            elif cap is not None and len(already) + len(new) >= cap:
                over_cap.append(source_id)
            else:
                new.append(source_id)
                fields = ", ".join(snapshot.output_fields(source_id))
                lines.append(
                    f"- {entry.model_name} ({source_id}): {entry.description} → returns: {fields}"
                )

        if unknown:
            ids = ", ".join(str(i) for i in unknown)
            lines.append(f"Unknown ids: {ids}. Use ids from the catalog.")
        if over_cap:
            ids = ", ".join(str(i) for i in over_cap)
            lines.append(f"Not loaded (limit {cap} reached): {ids}")
        if not lines:
            lines.append("No ids given. Use ids from the catalog.")

        message = ToolMessage(
            "\n".join(lines), tool_call_id=runtime.tool_call_id, name="load_tools"
        )
        update: dict = {"messages": [message]}
        if new:
            update["loaded_tools"] = new
        return Command(update=update)

    return load_tools
