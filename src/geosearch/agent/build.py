"""Assembles the Deep Agent from config, a chat model and a checkpointer.

Two harness modes (Stage 2 §9), selected by `cfg.agent.harness`:

  - "default": create_deep_agent's full built-in tool suite, plus our tool,
    prompt and middleware. Exists only to measure the baseline cost.
  - "trimmed": the lean harness we actually run. Its core tools are only
    {geo_describe_area, load_tools, submit_answer, ls, read_file, write_file}, and it swaps in
    a summarization middleware sized from our budget.

Registry tools (Stage 4): every tool in the catalog snapshot is registered, so a
loaded tool gets native tool calling with its real schema. The disclosure
middleware hides each one until `load_tools` loads it, and blocks calls to it
until then. The snapshot is fixed per built agent; AgentHolder rebuilds the
agent when the registry revision changes.

Our middleware order: area summary -> catalog -> disclosure -> summarizer ->
call limit -> allowlist -> ledger (last). The summarizer sits inside disclosure
on tool calls, so a blocked (unloaded) call never reaches it, and it rewrites a
registry result's rows into a cited summary before the message enters state.

Middleware ordering note for deepagents 0.7.21: user middleware whose `.name`
matches a base-stack middleware *replaces it in place* (so our FilesystemMiddleware
and SummarizationMiddleware override the defaults); brand-new middleware are
appended after the base core, before the tail. The token ledger is listed last
so it is innermost and measures the request exactly as the model receives it —
after the area line is appended and the tool set is trimmed.

Trimming mechanism (verified against 0.7.21): deepagents' own trimming levers
(`HarnessProfile.excluded_tools`, disabling the general-purpose subagent) are
keyed by `provider:model`, which would couple the code to a specific model. To
keep the harness model-agnostic we instead use a FilesystemMiddleware allowlist
for the file tools and our own ToolAllowlistMiddleware to drop everything else
(notably `task`). Note that deepagents 0.7.21 has no `write_todos` tool and no
`execute` tool without a sandbox backend, so neither appears here.
"""

from deepagents import FilesystemMiddleware, create_deep_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from geosearch.agent.answer import SubmitAnswerMiddleware
from geosearch.agent.context import AgentContext
from geosearch.agent.disclosure import CatalogMiddleware, DisclosureMiddleware
from geosearch.agent.ledger import TokenLedgerMiddleware
from geosearch.agent.middleware import AreaSummaryMiddleware, ToolAllowlistMiddleware
from geosearch.agent.prompts import SYSTEM_PROMPT
from geosearch.agent.state import GeoAgentState
from geosearch.agent.summarizer import ResultSummarizer, SummarizerMiddleware
from geosearch.agent.tools import geo_describe_area
from geosearch.agent.tools.answer import submit_answer
from geosearch.agent.tools.loading import make_load_tools
from geosearch.config import GeoConfig
from geosearch.registry.catalog import CatalogSnapshot

# The only core (non-registry) tools the trimmed harness offers the model.
TRIMMED_TOOLS = {
    "geo_describe_area", "load_tools", "submit_answer", "ls", "read_file", "write_file",
}  # fmt: skip
_TRIMMED_FILE_TOOLS = ["ls", "read_file", "write_file"]


def build_agent(
    cfg: GeoConfig,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    catalog: CatalogSnapshot | None = None,
    summarizer_model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """Build the agent (at startup, on a registry change, or per test). The
    model, checkpointer and catalog are injected so tests can supply a scripted
    model, an in-memory saver and a fake registry's snapshot. No catalog means
    an empty one: the agent then has only its core tools. The summarizer runs
    on `model` unless a separate `summarizer_model` is given (tests script the
    two apart)."""
    catalog = catalog or CatalogSnapshot()
    summarizer = ResultSummarizer(summarizer_model or model, cfg)
    middleware: list[AgentMiddleware] = [
        AreaSummaryMiddleware(),
        CatalogMiddleware(catalog),
        DisclosureMiddleware(catalog),
        SummarizerMiddleware(summarizer, catalog),
        SubmitAnswerMiddleware(),
    ]

    if cfg.agent.harness == "trimmed":
        # Summarize at a fraction of the input budget. Replaces the default
        # SummarizationMiddleware (matched by name), which can't know our window
        # because ChatOllama has no model profile.
        trigger_tokens = int(cfg.budget.summarize_at_fraction * cfg.budget.effective_input_budget)
        middleware += [
            FilesystemMiddleware(tools=_TRIMMED_FILE_TOOLS),
            SummarizationMiddleware(model=model, trigger=("tokens", trigger_tokens)),
        ]

    # exit_behavior="end": on the cap, finish gracefully with a synthetic message
    # rather than raising, so a turn always yields a response.
    middleware.append(
        ModelCallLimitMiddleware(
            run_limit=cfg.agent.max_model_calls_per_turn,
            exit_behavior="end",
        )
    )

    if cfg.agent.harness == "trimmed":
        # Trims the harness built-ins; registry tools pass (disclosure governs them).
        registry_names = {entry.model_name for entry in catalog.tools}
        middleware.append(ToolAllowlistMiddleware(TRIMMED_TOOLS | registry_names))

    # Last, so it is innermost and measures the final request.
    middleware.append(TokenLedgerMiddleware())

    return create_deep_agent(
        model=model,
        tools=[
            geo_describe_area,
            make_load_tools(catalog),
            submit_answer,
            *catalog.resolved_tools(),
        ],
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
        state_schema=GeoAgentState,
        context_schema=AgentContext,
        checkpointer=checkpointer,
    )
