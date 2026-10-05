"""Keeps the current area in front of the model on every call.

Each model call, this appends one line — `Current area: <summary>` — to the
existing system message. Two deliberate choices:

  - It *appends*, never replaces, so the Deep Agents base prompt and our own
    system prompt survive untouched.
  - It lives in the system message rather than a first user message, because
    summarization of old turns could otherwise drop the user message that
    carried the area, leaving the model with no idea what area it is discussing.

The summary is read from state, never from WKT — the model still never sees
geometry (invariant 1).
"""

from collections.abc import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


class AreaSummaryMiddleware(AgentMiddleware):
    """Appends the area summary line to the system prompt for each model call."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        conversation = request.state.get("conversation") or {}
        summary = conversation.get("area_summary")
        if not summary:
            return handler(request)

        base = request.system_prompt or ""
        line = f"Current area: {summary}"
        new_text = f"{base}\n{line}" if base else line
        return handler(request.override(system_message=SystemMessage(new_text)))
