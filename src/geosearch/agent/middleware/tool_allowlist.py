"""Restricts the tools offered to the model to a fixed allowlist.

This is our model-agnostic stand-in for deepagents' `HarnessProfile.excluded_tools`.
We avoid the profile mechanism on purpose: profiles are keyed by `provider:model`,
so using one would couple the harness to a specific model — the opposite of this
project's "model is config" goal. Filtering `request.tools` in `wrap_model_call`
achieves the same end (the model only ever sees the allowed tools) for any
provider.

It runs near the inside of the stack, just before the token ledger, so the
ledger measures the already-trimmed tool set.
"""

from collections.abc import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


class ToolAllowlistMiddleware(AgentMiddleware):
    """Drops any offered tool whose name is not in `allow`."""

    def __init__(self, allow: set[str]):
        super().__init__()
        self.allow = allow

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        kept = [tool for tool in request.tools if tool.name in self.allow]
        return handler(request.override(tools=kept))
