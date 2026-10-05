"""The only module that knows about model providers (Stage 2 invariant 5).

Everything else in the agent receives a ready-made `BaseChatModel`, so switching
provider, model or context window is a config change and nothing imports a
specific provider class but this file.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from geosearch.config import ContextBudgetConfig, LLMConfig


def build_chat_model(llm: LLMConfig, budget: ContextBudgetConfig) -> BaseChatModel:
    """Construct the chat model named by config.

    Why ChatOllama rather than ChatOpenAI against Ollama's /v1 endpoint: the
    OpenAI-compatible API can't set the context size per request, and Ollama
    silently defaults Gemma 4 to a ~4K window. ChatOllama lets us pass num_ctx
    from our budget config so the configured window is actually in effect.
    """
    if llm.provider == "ollama":
        # `reasoning` is ChatOllama's name for Gemma 4's thinking mode (verified
        # against langchain-ollama 1.1.0; there is no `thinking`/`think` field).
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=llm.model,
            base_url=llm.base_url,
            num_ctx=budget.context_window,
            num_predict=budget.max_output_tokens,
            temperature=llm.temperature,
            keep_alive=llm.keep_alive,
            reasoning=llm.thinking,
            client_kwargs={"timeout": llm.timeout_s},
        )

    if llm.provider == "openai_compatible":
        # use_responses_api=False: most self-hosted servers (vLLM, Ollama /v1)
        # speak plain chat completions, not OpenAI's Responses API. We always
        # pass a model instance, never a "provider:model" string, so Deep Agents
        # never routes this to the Responses API itself.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=llm.model,
            base_url=llm.base_url,
            api_key=llm.api_key.get_secret_value(),
            max_tokens=budget.max_output_tokens,
            temperature=llm.temperature,
            timeout=llm.timeout_s,
            use_responses_api=False,
        )

    raise ValueError(f"unknown llm.provider: {llm.provider!r}")  # unreachable via config
