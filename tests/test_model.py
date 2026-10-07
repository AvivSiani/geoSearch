"""The model factory must build the model from config alone, offline.

These construct the chat-model objects but never call them, so no server or GPU
is needed (Stage 2 invariant 6). The server and model name are agentkit_619's
AGENTKIT_MODEL_* variables, set here per test.
"""

import pytest
from langchain_openai import ChatOpenAI

from geosearch.agent.model import build_chat_model
from geosearch.config import ContextBudgetConfig, LLMConfig


@pytest.fixture(autouse=True)
def agentkit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTKIT_MODEL_URL", "http://host:8000/v1")
    monkeypatch.setenv("AGENTKIT_MODEL_NAME", "gemma4:12b")
    monkeypatch.setenv("AGENTKIT_MODEL_VERIFY_SSL", "true")


def test_builds_plain_chat_from_agentkit_env() -> None:
    budget = ContextBudgetConfig(max_output_tokens=512)
    model = build_chat_model(LLMConfig(provider="openai_compatible"), budget)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gemma4:12b"
    assert model.openai_api_base == "http://host:8000/v1"
    assert model.max_tokens == 512
    # Self-hosted servers speak chat completions, not the Responses API.
    assert model.use_responses_api is False
    assert not model.extra_body


def test_ollama_turns_thinking_off_and_keeps_alive() -> None:
    model = build_chat_model(LLMConfig(provider="ollama", keep_alive="5m"), ContextBudgetConfig())
    # Without reasoning_effort "none", Ollama's /v1 thinks by default.
    assert model.extra_body == {"keep_alive": "5m", "reasoning_effort": "none"}


def test_ollama_thinking_leaves_reasoning_on() -> None:
    model = build_chat_model(LLMConfig(provider="ollama", thinking=True), ContextBudgetConfig())
    assert "reasoning_effort" not in model.extra_body


def test_missing_agentkit_env_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTKIT_MODEL_URL")
    with pytest.raises(RuntimeError, match="AGENTKIT_MODEL_URL"):
        build_chat_model(LLMConfig(), ContextBudgetConfig())


def test_unknown_provider_raises() -> None:
    cfg = LLMConfig(provider="ollama")
    object.__setattr__(cfg, "provider", "bogus")  # bypass Literal to hit the guard
    with pytest.raises(ValueError, match="unknown llm.provider"):
        build_chat_model(cfg, ContextBudgetConfig())
