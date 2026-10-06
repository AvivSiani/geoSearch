"""Application configuration.

Config is validated at startup via pydantic; invalid config fails fast with a
clear message rather than surfacing as a confusing runtime error later.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PointBufferConfig(BaseModel):
    """How a Point input is turned into an area (see geo/buffer.py)."""

    strategy: Literal["geodesic_circle"] = "geodesic_circle"
    default_radius_m: float = 10.0
    min_radius_m: float = 1.0
    max_radius_m: float = 5_000.0
    quad_segs: int = 16
    allow_request_override: bool = False

    @model_validator(mode="after")
    def _check_radius_bounds(self) -> "PointBufferConfig":
        if not (0 < self.min_radius_m <= self.default_radius_m <= self.max_radius_m):
            raise ValueError(
                "point_buffer radii must satisfy "
                "0 < min_radius_m <= default_radius_m <= max_radius_m, got "
                f"min={self.min_radius_m}, default={self.default_radius_m}, "
                f"max={self.max_radius_m}"
            )
        if self.quad_segs < 4:
            raise ValueError(f"point_buffer.quad_segs must be >= 4, got {self.quad_segs}")
        return self


class LimitsConfig(BaseModel):
    """Bounds used by request/checks.py to reject oversized or malformed input."""

    max_wkt_bytes: int = 100_000
    max_vertices: int = 10_000
    max_area_km2: float = 100.0
    max_prompt_chars: int = 2_000

    @model_validator(mode="after")
    def _check_positive(self) -> "LimitsConfig":
        for name in ("max_wkt_bytes", "max_vertices", "max_area_km2", "max_prompt_chars"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"limits.{name} must be > 0, got {value}")
        return self


class AreaStoreConfig(BaseModel):
    """Bounds for the in-memory area store (see geo/area_store.py)."""

    max_entries: int = 10_000

    @model_validator(mode="after")
    def _check_positive(self) -> "AreaStoreConfig":
        if self.max_entries <= 0:
            raise ValueError(f"area_store.max_entries must be > 0, got {self.max_entries}")
        return self


class LLMConfig(BaseModel):
    """Which model to talk to and how. Changing provider, model or server is a
    config change, never a code change (Stage 2 invariant 4). Only agent/model.py
    reads this (invariant 5)."""

    provider: Literal["ollama", "openai_compatible"] = "ollama"
    model: str = "gemma4:12b"
    base_url: str = "http://localhost:11434"  # vLLM example: http://host:8000/v1
    api_key: SecretStr = SecretStr("EMPTY")  # openai_compatible only
    temperature: float = 0.0
    thinking: bool = False  # Gemma 4 thinking mode (ChatOllama `reasoning`)
    keep_alive: str = "10m"  # ollama only
    timeout_s: float = 120.0


class ContextBudgetConfig(BaseModel):
    """The small-context discipline, entirely in numbers. Defaults are sized for
    a 12B model at a 16K window (stage-2 §6)."""

    context_window: int = 16_384  # Ollama num_ctx / server max length
    max_output_tokens: int = 1_024
    safety_margin_tokens: int = 2_048
    input_budget_tokens: int | None = None  # None -> window - output - margin
    summarize_at_fraction: float = 0.75  # of the input budget
    truncation_warn_ratio: float = 0.6  # token ledger, stage-2 §11

    @property
    def effective_input_budget(self) -> int:
        """How many tokens a single model call's input may use. Either set
        explicitly, or what's left of the window after output and safety margin."""
        if self.input_budget_tokens is not None:
            return self.input_budget_tokens
        return self.context_window - self.max_output_tokens - self.safety_margin_tokens

    @model_validator(mode="after")
    def _check_budget(self) -> "ContextBudgetConfig":
        if self.max_output_tokens >= self.context_window:
            raise ValueError(
                "budget.max_output_tokens must be < context_window, got "
                f"{self.max_output_tokens} >= {self.context_window}"
            )
        if not (0 < self.summarize_at_fraction < 1):
            raise ValueError(
                "budget.summarize_at_fraction must be in (0, 1), got "
                f"{self.summarize_at_fraction}"
            )
        if self.effective_input_budget <= 0:
            raise ValueError(
                "budget.effective_input_budget must be > 0; window minus output "
                f"minus margin was {self.effective_input_budget}"
            )
        return self


class ConversationConfig(BaseModel):
    """Multi-turn conversation bounds. Conversations stay in memory; a mongodb
    store is not planned (Stage 3 uses MongoDB for the tool registry only)."""

    store: Literal["memory", "mongodb"] = "memory"
    max_turns: int = 20
    idle_ttl_minutes: int = 60

    @model_validator(mode="after")
    def _check_bounds(self) -> "ConversationConfig":
        if self.max_turns < 1:
            raise ValueError(f"conversation.max_turns must be >= 1, got {self.max_turns}")
        if self.idle_ttl_minutes <= 0:
            raise ValueError(
                f"conversation.idle_ttl_minutes must be > 0, got {self.idle_ttl_minutes}"
            )
        return self


class AgentConfig(BaseModel):
    """How the Deep Agents harness is assembled. `trimmed` is the lean default;
    `default` exists only to measure the baseline harness cost (stage-2 §9)."""

    harness: Literal["default", "trimmed"] = "trimmed"
    max_model_calls_per_turn: int = 8

    @model_validator(mode="after")
    def _check_calls(self) -> "AgentConfig":
        if self.max_model_calls_per_turn < 1:
            raise ValueError(
                "agent.max_model_calls_per_turn must be >= 1, got "
                f"{self.max_model_calls_per_turn}"
            )
        return self


class MongoConfig(BaseModel):
    """Where the tool registry lives (Stage 3). Conversations stay in memory."""

    uri: str = "mongodb://localhost:27017"
    database: str = "geosearch"
    server_selection_timeout_ms: int = 2_000

    @model_validator(mode="after")
    def _check_timeout(self) -> "MongoConfig":
        if self.server_selection_timeout_ms <= 0:
            raise ValueError(
                "mongo.server_selection_timeout_ms must be > 0, got "
                f"{self.server_selection_timeout_ms}"
            )
        return self


class RegistryConfig(BaseModel):
    """How the tool registry is seeded, checked and used (Stage 3)."""

    seeds_dir: Path = Path("registry/seeds")
    seed_demo: bool = False
    max_inline_result_chars: int = 1_500
    required: bool = True  # fail startup if MongoDB is unreachable
    strict_startup: bool = True  # a stored source_id without a handler fails startup

    @model_validator(mode="after")
    def _check_limit(self) -> "RegistryConfig":
        # Room for a short summary plus two file-pointer lines.
        if self.max_inline_result_chars < 300:
            raise ValueError(
                "registry.max_inline_result_chars must be >= 300, got "
                f"{self.max_inline_result_chars}"
            )
        return self


class DisclosureConfig(BaseModel):
    """Progressive disclosure of registry tools (Stage 4)."""

    max_loaded_tools: int | None = None  # None = no cap; the ledger's over_budget is the signal
    catalog_warn_tokens: int = 600  # the ledger flags a catalog block larger than this

    @model_validator(mode="after")
    def _check_bounds(self) -> "DisclosureConfig":
        if self.max_loaded_tools is not None and self.max_loaded_tools < 1:
            raise ValueError(
                f"disclosure.max_loaded_tools must be >= 1 or unset, got {self.max_loaded_tools}"
            )
        if self.catalog_warn_tokens <= 0:
            raise ValueError(
                f"disclosure.catalog_warn_tokens must be > 0, got {self.catalog_warn_tokens}"
            )
        return self


class PlacesConfig(BaseModel):
    """The places provider behind tools 9101/9102 (Stage 5). `replay` is the
    default so a dev run or test never needs a key or the network; `google` is
    the demo live provider."""

    provider: Literal["google", "replay"] = "replay"
    api_key: SecretStr | None = None  # google only
    base_url: str = "https://places.googleapis.com/v1"
    timeout_s: float = 10.0
    max_results: int = 20  # one page; Text Search (New) caps pageSize at 20
    fixtures_dir: Path = Path("fixtures/places")  # replay only

    @model_validator(mode="after")
    def _check(self) -> "PlacesConfig":
        if self.provider == "google" and not (self.api_key and self.api_key.get_secret_value()):
            raise ValueError("places.api_key is required when places.provider is 'google'")
        if not (1 <= self.max_results <= 20):
            raise ValueError(f"places.max_results must be in [1, 20], got {self.max_results}")
        if self.timeout_s <= 0:
            raise ValueError(f"places.timeout_s must be > 0, got {self.timeout_s}")
        return self


class SummarizerConfig(BaseModel):
    """The summarizer that turns tool rows into a cited summary (Stage 5). It
    runs on the main agent's model; only its sizes live here."""

    chunk_tokens: int = 2_500  # rows per summarizer call, estimated tokens
    max_chunks: int = 4  # rows beyond this many chunks are left out (and counted)
    max_output_tokens: int = 400

    @model_validator(mode="after")
    def _check(self) -> "SummarizerConfig":
        for name in ("chunk_tokens", "max_chunks", "max_output_tokens"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"summarizer.{name} must be >= 1, got {value}")
        return self


class GeoConfig(BaseSettings):
    """Root config. Env prefix GEOSEARCH_, nested delimiter __.

    Example override: GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M=1000
    """

    model_config = SettingsConfigDict(env_prefix="GEOSEARCH_", env_nested_delimiter="__")

    point_buffer: PointBufferConfig = Field(default_factory=PointBufferConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    area_store: AreaStoreConfig = Field(default_factory=AreaStoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    registry: RegistryConfig = Field(default_factory=RegistryConfig)
    disclosure: DisclosureConfig = Field(default_factory=DisclosureConfig)
    places: PlacesConfig = Field(default_factory=PlacesConfig)
    summarizer: SummarizerConfig = Field(default_factory=SummarizerConfig)

    @model_validator(mode="after")
    def _check_summarizer_fits_budget(self) -> "GeoConfig":
        """The summarizer shares the main model's window: a chunk of rows (map)
        and all partial summaries together (merge) must each fit one call."""
        budget = self.budget.effective_input_budget
        s = self.summarizer
        if s.chunk_tokens >= budget:
            raise ValueError(
                f"summarizer.chunk_tokens must be < the input budget ({budget}), "
                f"got {s.chunk_tokens}"
            )
        if s.max_chunks * s.max_output_tokens >= budget:
            raise ValueError(
                "summarizer.max_chunks * summarizer.max_output_tokens must be < the input "
                f"budget ({budget}), got {s.max_chunks * s.max_output_tokens}"
            )
        return self
