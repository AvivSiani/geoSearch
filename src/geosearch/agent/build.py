"""Assembles the Deep Agent from config, a chat model and a checkpointer.

Middleware order matters — the first entry is the outermost wrapper. Our order
(Stage 2 §9):

  1. AreaSummaryMiddleware (ours): keeps the area in the system prompt.
  2. Summarization: added by create_deep_agent by default (configured from our
     budget in step 6).
  3. ModelCallLimitMiddleware: caps model calls per turn.
  4. Token ledger (ours): added last in step 4, so it measures the request
     exactly as the model receives it.

Step 3 builds the `default` harness only; the trimmed harness arrives in step 6.
"""

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from geosearch.agent.context import AgentContext
from geosearch.agent.ledger import TokenLedgerMiddleware
from geosearch.agent.middleware import AreaSummaryMiddleware
from geosearch.agent.prompts import SYSTEM_PROMPT
from geosearch.agent.state import GeoAgentState
from geosearch.agent.tools import geo_describe_area
from geosearch.config import GeoConfig


def build_agent(
    cfg: GeoConfig,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Build the agent once (at app startup, or per test). The model and
    checkpointer are injected so tests can supply a scripted model and an
    in-memory saver."""
    middleware: list[AgentMiddleware] = [
        AreaSummaryMiddleware(),
        # exit_behavior="end": on the cap, finish gracefully with a synthetic
        # message rather than raising, so a turn always yields a response.
        ModelCallLimitMiddleware(
            run_limit=cfg.agent.max_model_calls_per_turn,
            exit_behavior="end",
        ),
        # Last, so it measures the request exactly as the model receives it.
        TokenLedgerMiddleware(),
    ]

    return create_deep_agent(
        model=model,
        tools=[geo_describe_area],
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
        state_schema=GeoAgentState,
        context_schema=AgentContext,
        checkpointer=checkpointer,
    )
