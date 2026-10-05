"""Running a single turn: build the turn's state, invoke the agent, read the
answer. Conversations (loading prior state, follow-ups, the registry) are layered
on top of this in step 7.
"""

from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph

from geosearch.agent.context import AgentContext

StoppedReason = Literal["finished", "call_limit"]

# The ModelCallLimitMiddleware (exit_behavior="end") ends a run by appending an
# AIMessage with this prefix. Pinned to langchain==1.4.3; re-verify on upgrade.
_CALL_LIMIT_PREFIX = "Model call limits exceeded"


@dataclass
class TurnOutcome:
    conversation_id: str
    turn: int
    request_id: str
    area_summary: str
    answer: str
    stopped_reason: StoppedReason
    state: dict[str, Any]  # final graph state, for the ledger and conversations


def build_new_turn_state(
    *,
    conversation_id: str,
    area_id: str,
    area_wkt: str,
    area_summary: str,
    request_id: str,
    prompt: str,
    turn: int,
) -> dict[str, Any]:
    """The state update for a turn: the user's prompt plus fresh `request` and
    `search`. `conversation` carries the fixed area; `intent` and
    `loaded_capabilities` are left for the checkpointer to persist across turns."""
    return {
        "messages": [{"role": "user", "content": prompt}],
        "conversation": {
            "conversation_id": conversation_id,
            "area_id": area_id,
            "area_wkt": area_wkt,
            "area_summary": area_summary,
            "turn": turn,
        },
        "request": {"request_id": request_id, "prompt": prompt},
        "search": {"iteration": 0, "candidate_count": 0, "status": "idle"},
    }


def _last_ai_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message.text if hasattr(message, "text") else str(message.content)
    return ""


def _was_call_limited(messages: list[Any]) -> bool:
    if not messages:
        return False
    last = messages[-1]
    return isinstance(last, AIMessage) and str(last.content).startswith(_CALL_LIMIT_PREFIX)


def invoke_turn(
    agent: CompiledStateGraph,
    context: AgentContext,
    state_update: dict[str, Any],
    *,
    thread_id: str | None = None,
) -> TurnOutcome:
    """Invoke the agent for one turn and package the result.

    A fresh AgentContext is passed per turn (invariant: never persisted, never
    shown to the model). The thread_id routes checkpointing for conversations.
    """
    config = {"configurable": {"thread_id": thread_id}} if thread_id else {}
    result = agent.invoke(state_update, context=context, config=config)

    messages = result.get("messages", [])
    conversation = result["conversation"]
    request = result["request"]
    return TurnOutcome(
        conversation_id=conversation["conversation_id"],
        turn=conversation["turn"],
        request_id=request["request_id"],
        area_summary=conversation["area_summary"],
        answer=_last_ai_text(messages),
        stopped_reason="call_limit" if _was_call_limited(messages) else "finished",
        state=result,
    )
