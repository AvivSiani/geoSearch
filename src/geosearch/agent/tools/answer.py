"""`submit_answer`: how the agent finishes a turn (Stage 5).

The answer's items are checked against data, not trusted: every id must be a
conversation item (something a tool actually returned, inside the area). An
unknown id is an error the model can fix with another call; nothing is stored.
On success the text keeps only citations of the submitted ids, and
SubmitAnswerMiddleware ends the turn before the next model call.

Not `return_direct=True`: LangChain ends the run after a return-direct tool even
when it returns an error, which would leave no chance to fix a bad id.
"""

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from geosearch.agent.citations import strip_invalid_ids
from geosearch.agent.context import AgentContext
from geosearch.agent.state import GeoAgentState


def _reply(runtime: ToolRuntime, text: str, *, error: bool = False) -> ToolMessage:
    return ToolMessage(
        text,
        tool_call_id=runtime.tool_call_id,
        name="submit_answer",
        status="error" if error else "success",
    )


@tool
def submit_answer(
    text: str, item_ids: list[str], runtime: ToolRuntime[AgentContext, GeoAgentState]
) -> Command | ToolMessage:
    """Finish the turn with your answer. `text` is the answer for the user, citing
    items as [i3]; `item_ids` lists the items the answer presents (empty if none)."""
    if not text.strip():
        return _reply(runtime, "Error: text is empty. Write the answer for the user.", error=True)
    known = runtime.state.get("items") or {}
    ids = list(dict.fromkeys(item_ids))  # de-duplicate, keep order
    if unknown := [i for i in ids if i not in known]:
        return _reply(
            runtime,
            f"Error: unknown item ids: {', '.join(unknown)}. Use only ids from tool results.",
            error=True,
        )
    answer = {"text": strip_invalid_ids(text.strip(), set(ids)), "item_ids": ids}
    return Command(update={"answer": answer, "messages": [_reply(runtime, "Answer submitted.")]})
