import pytest
from pydantic import ValidationError

from geosearch.config import (
    AgentConfig,
    ContextBudgetConfig,
    ConversationConfig,
    GeoConfig,
    LimitsConfig,
    LLMConfig,
    MongoConfig,
    PointBufferConfig,
    RegistryConfig,
)


def test_defaults(cfg: GeoConfig) -> None:
    assert cfg.point_buffer.default_radius_m == 10.0
    assert cfg.point_buffer.min_radius_m == 1.0
    assert cfg.point_buffer.max_radius_m == 5_000.0
    assert cfg.point_buffer.strategy == "geodesic_circle"
    assert cfg.point_buffer.allow_request_override is False
    assert cfg.limits.max_area_km2 == 100.0
    assert cfg.area_store.max_entries == 10_000


def test_stage2_defaults(cfg: GeoConfig) -> None:
    assert cfg.llm.provider == "ollama"
    assert cfg.llm.model == "gemma4:12b"
    assert cfg.llm.base_url == "http://localhost:11434"
    assert cfg.budget.context_window == 16_384
    assert cfg.budget.effective_input_budget == 13_312
    assert cfg.conversation.store == "memory"
    assert cfg.conversation.max_turns == 20
    assert cfg.agent.harness == "trimmed"
    assert cfg.agent.max_model_calls_per_turn == 8


def test_api_key_is_secret(cfg: GeoConfig) -> None:
    # The key must not leak through repr/str; it reads back only explicitly.
    assert "EMPTY" not in repr(cfg.llm.api_key)
    assert cfg.llm.api_key.get_secret_value() == "EMPTY"


def test_effective_input_budget_explicit_overrides_derived() -> None:
    budget = ContextBudgetConfig(input_budget_tokens=5_000)
    assert budget.effective_input_budget == 5_000


def test_budget_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_BUDGET__CONTEXT_WINDOW", "8192")
    cfg = GeoConfig(_env_file=None)
    assert cfg.budget.context_window == 8192
    assert cfg.budget.effective_input_budget == 8192 - 1024 - 2048


def test_output_tokens_must_fit_window() -> None:
    with pytest.raises(ValidationError, match="max_output_tokens"):
        ContextBudgetConfig(context_window=1_024, max_output_tokens=1_024)


def test_effective_budget_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="effective_input_budget"):
        ContextBudgetConfig(
            context_window=4_096, max_output_tokens=2_048, safety_margin_tokens=2_048
        )


def test_summarize_fraction_bounds() -> None:
    with pytest.raises(ValidationError, match="summarize_at_fraction"):
        ContextBudgetConfig(summarize_at_fraction=1.0)


def test_max_turns_at_least_one() -> None:
    with pytest.raises(ValidationError, match="max_turns"):
        ConversationConfig(max_turns=0)


def test_max_model_calls_at_least_one() -> None:
    with pytest.raises(ValidationError, match="max_model_calls_per_turn"):
        AgentConfig(max_model_calls_per_turn=0)


def test_llm_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_LLM__PROVIDER", "openai_compatible")
    monkeypatch.setenv("GEOSEARCH_LLM__MODEL", "some-model")
    cfg = GeoConfig(_env_file=None)
    assert isinstance(cfg.llm, LLMConfig)
    assert cfg.llm.provider == "openai_compatible"
    assert cfg.llm.model == "some-model"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M", "1000")
    cfg = GeoConfig(_env_file=None)
    assert cfg.point_buffer.default_radius_m == 1000.0


def test_invalid_radius_bounds_fails_fast() -> None:
    with pytest.raises(ValidationError, match="min_radius_m"):
        PointBufferConfig(min_radius_m=50.0, default_radius_m=10.0)


def test_invalid_quad_segs_fails_fast() -> None:
    with pytest.raises(ValidationError, match="quad_segs"):
        PointBufferConfig(quad_segs=2)


def test_invalid_limit_fails_fast() -> None:
    with pytest.raises(ValidationError, match="max_area_km2"):
        LimitsConfig(max_area_km2=0)


def test_stage3_defaults(cfg: GeoConfig) -> None:
    assert cfg.mongo.uri == "mongodb://localhost:27017"
    assert cfg.mongo.database == "geosearch"
    assert cfg.registry.max_inline_result_chars == 1_500
    assert cfg.registry.required is True
    assert cfg.registry.strict_startup is True
    assert cfg.registry.seed_demo is False


def test_registry_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_REGISTRY__REQUIRED", "false")
    monkeypatch.setenv("GEOSEARCH_MONGO__DATABASE", "other")
    loaded = GeoConfig(_env_file=None)
    assert loaded.registry.required is False
    assert loaded.mongo.database == "other"


def test_invalid_stage3_config_fails_fast() -> None:
    with pytest.raises(ValidationError):
        MongoConfig(server_selection_timeout_ms=0)
    with pytest.raises(ValidationError):
        RegistryConfig(max_inline_result_chars=10)
