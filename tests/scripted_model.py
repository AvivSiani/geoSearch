"""A deterministic stand-in for a real chat model, so the whole agent can be
tested in pytest with no server and no GPU (Stage 2 invariant 6).

We write our own rather than use LangChain's fakes because the agent calls
`bind_tools`, which the fakes don't reliably support. It simply replays a
prepared list of AIMessages — including tool_calls and fake usage_metadata —
one per model call, which is exactly what the harness needs to be exercised.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Replays `responses` in order, one per call. The last response repeats if
    the agent asks for more (a model that keeps emitting tool calls, say), which
    is what the call-limit test relies on.

    `calls` records the messages each invocation received, so tests can assert on
    exactly what the model saw (e.g. that no message ever contains WKT).
    """

    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    index: int = 0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        # The agent binds tools before each call; a scripted model ignores them
        # but must stay a model (return self), not a generic Runnable.
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        i = min(self.index, len(self.responses) - 1)
        self.index += 1
        message = self.responses[i]
        return ChatResult(generations=[ChatGeneration(message=message)])


def ai(content: str, *, tool_calls: list[dict] | None = None, tokens: int = 10) -> AIMessage:
    """Build a scripted AIMessage with plausible usage_metadata (so the ledger
    has reported numbers to record)."""
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata={
            "input_tokens": tokens,
            "output_tokens": max(1, len(content) // 4),
            "total_tokens": tokens + max(1, len(content) // 4),
        },
    )


def tool_call(name: str, args: dict | None = None, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}
