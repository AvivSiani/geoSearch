"""The only module that knows about model providers (Stage 2 invariant 5).

Everything else in the agent receives a ready-made `BaseChatModel`, so switching
provider, model or context window is a config change and nothing builds a
specific provider class but this file.

The model comes from agentkit_619's `get_model`: a ChatOpenAI for the team's
OpenAI-compatible server, configured by its AGENTKIT_MODEL_* variables. Ollama
is a temporary dev server reached through its OpenAI-compatible /v1 endpoint.
"""

from typing import Any

from agentkit_619 import get_model
from langchain_core.language_models.chat_models import BaseChatModel

from geosearch.config import ContextBudgetConfig, LLMConfig


def build_chat_model(llm: LLMConfig, budget: ContextBudgetConfig) -> BaseChatModel:
    """Construct the chat model named by config (fails fast if AGENTKIT_MODEL_URL
    or AGENTKIT_MODEL_NAME is unset).

    Ollama caveat: /v1 ignores `num_ctx`, so the window cannot be set per
    request; Ollama loads the model at its own default (4096 for gemma4 on
    0.35.1). Give it `budget.context_window` on the server side: a model tag
    built with `PARAMETER num_ctx`, or OLLAMA_CONTEXT_LENGTH. The smoke script
    checks it.
    """
    if llm.provider not in ("ollama", "openai_compatible"):
        raise ValueError(f"unknown llm.provider: {llm.provider!r}")  # unreachable via config
    return get_model(
        max_tokens=budget.max_output_tokens,
        temperature=llm.temperature,
        extra_body=_extra_body(llm),
    )


def _extra_body(llm: LLMConfig) -> dict[str, Any] | None:
    """Server-specific request fields. Ollama's /v1 thinks by default (and can
    spend all of `max_tokens` on it, leaving empty content) unless sent
    `reasoning_effort: "none"`; it also takes `keep_alive` (both verified on
    Ollama 0.35.1)."""
    if llm.provider != "ollama":
        return None
    body: dict[str, Any] = {"keep_alive": llm.keep_alive}
    if not llm.thinking:
        body["reasoning_effort"] = "none"
    return body
