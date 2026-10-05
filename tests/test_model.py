"""The model factory must build either provider from config alone, offline.

These construct the chat-model objects but never call them, so no server or GPU
is needed (Stage 2 invariant 6).
"""

import pytest
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from geosearch.agent.model import build_chat_model
from geosearch.config import ContextBudgetConfig, LLMConfig


def test_builds_ollama_with_window_from_budget() -> None:
    budget = ContextBudgetConfig(context_window=16_384, max_output_tokens=1_024)
    model = build_chat_model(LLMConfig(provider="ollama", model="gemma4:12b"), budget)
    assert isinstance(model, ChatOllama)
    # The whole point of ChatOllama: num_ctx actually carries our window.
    assert model.num_ctx == 16_384
    assert model.num_predict == 1_024
    assert model.reasoning is False


def test_ollama_thinking_maps_to_reasoning() -> None:
    model = build_chat_model(LLMConfig(provider="ollama", thinking=True), ContextBudgetConfig())
    assert isinstance(model, ChatOllama)
    assert model.reasoning is True


def test_builds_openai_compatible_plain_chat() -> None:
    budget = ContextBudgetConfig(max_output_tokens=512)
    model = build_chat_model(
        LLMConfig(provider="openai_compatible", model="x", base_url="http://host:8000/v1"),
        budget,
    )
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "http://host:8000/v1"
    assert model.max_tokens == 512
    # Self-hosted servers speak chat completions, not the Responses API.
    assert model.use_responses_api is False


def test_switching_provider_is_config_only() -> None:
    # Same factory call, only config differs -> different model class.
    budget = ContextBudgetConfig()
    a = build_chat_model(LLMConfig(provider="ollama"), budget)
    b = build_chat_model(LLMConfig(provider="openai_compatible"), budget)
    assert type(a) is not type(b)


def test_unknown_provider_raises() -> None:
    cfg = LLMConfig(provider="ollama")
    object.__setattr__(cfg, "provider", "bogus")  # bypass Literal to hit the guard
    with pytest.raises(ValueError, match="unknown llm.provider"):
        build_chat_model(cfg, ContextBudgetConfig())
