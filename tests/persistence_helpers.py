"""Builds a RequestRunner on a chosen persistence, wired as create_app wires it,
with an injectable clock and models (Stage 6 tests)."""

from langchain_core.language_models.chat_models import BaseChatModel
from pymongo.database import Database

from geosearch.agent.conversations import ConversationRegistry
from geosearch.agent.holder import AgentHolder
from geosearch.agent.persistence import Persistence
from geosearch.agent.run import RequestRunner
from geosearch.clock import Clock, utc_now
from geosearch.config import GeoConfig
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.registry.catalog import CatalogCache


def mongo_cfg(db: Database) -> GeoConfig:
    """Default config on the mongodb backend, in the test's throwaway database."""
    cfg = GeoConfig(_env_file=None)
    cfg.conversation.store = "mongodb"
    cfg.mongo.database = db.name
    return cfg


def build_runner(
    cfg: GeoConfig,
    persistence: Persistence,
    model: BaseChatModel,
    *,
    catalog: CatalogCache | None = None,
    summarizer: BaseChatModel | None = None,
    clock: Clock = utc_now,
) -> RequestRunner:
    store = persistence.area_store
    registry = ConversationRegistry(
        cfg.conversation, persistence.checkpointer, clock=clock, store=persistence.conversations
    )
    agents = AgentHolder(cfg, model, persistence.checkpointer, catalog, summarizer)
    return RequestRunner(
        cfg, agents, registry, store, AreaOps(store),
        STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer),
        keep_alive=persistence.keep_alive, clock=clock,
    )  # fmt: skip
