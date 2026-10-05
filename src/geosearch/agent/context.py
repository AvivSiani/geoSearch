"""Runtime context: dependencies the agent and its tools need, but which must
never be persisted or shown to the model.

Why a runtime context rather than module globals:
  - it is never checkpointed and never serialized into a message;
  - it can be swapped in tests (a fake AreaOps, a throwaway config);
  - it makes the agent's real dependencies explicit instead of hidden.

It is passed with `context_schema=AgentContext` at build time and a fresh
instance per turn via `agent.invoke(..., context=AgentContext(...))`.
"""

from dataclasses import dataclass, field

from geosearch.agent.ledger import TokenLedger
from geosearch.config import GeoConfig
from geosearch.geo.ops import AreaOps


@dataclass
class AgentContext:
    """Per-invocation dependencies for tools and middleware."""

    area_ops: AreaOps  # reads geometry by area_id (Stage 1)
    cfg: GeoConfig
    ledger: TokenLedger = field(default_factory=TokenLedger)  # fresh per turn
