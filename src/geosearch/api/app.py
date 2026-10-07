"""The app factory. request/ and geo/ hold the deterministic logic; agent/ holds
the agent; this module wires them together once and exposes the route.

The model, persistence (checkpointer, records, areas), conversation registry, agent
and tool catalog are built once at startup and kept on app.state. A model
instance can be injected (tests pass a scripted model; nothing here imports a
provider class — agent/model.py owns that), and so can the tool registry (the
Stage 1-2 tests pass an in-memory one, so they never need MongoDB).

There is deliberately no module-level `app` here: building one at import would
connect to MongoDB on import, including for tests that only want create_app.
The ASGI entry point is api/main.py (`uvicorn geosearch.api.main:app`)."""

from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel

from geosearch.agent.conversations import ConversationRegistry
from geosearch.agent.holder import AgentHolder
from geosearch.agent.model import build_chat_model
from geosearch.agent.persistence import open_persistence
from geosearch.agent.run import RequestRunner
from geosearch.api.errors import register_exception_handlers
from geosearch.api.routes import router
from geosearch.config import GeoConfig
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.registry.startup import open_catalog
from geosearch.registry.store import Registry


def create_app(
    cfg: GeoConfig | None = None,
    model: BaseChatModel | None = None,
    tool_registry: Registry | None = None,
) -> FastAPI:
    cfg = cfg or GeoConfig()
    catalog = open_catalog(cfg, tool_registry)  # first: fail fast before building the agent
    persistence = open_persistence(cfg)  # fails fast too: never a silent memory fallback
    store = persistence.area_store
    ops = AreaOps(store)
    buffer_strategy = STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)

    model = model or build_chat_model(cfg.llm, cfg.budget)
    checkpointer = persistence.checkpointer
    registry = ConversationRegistry(
        cfg.conversation, checkpointer, store=persistence.conversations
    )
    agents = AgentHolder(cfg, model, checkpointer, catalog)  # rebuilds on registry change
    runner = RequestRunner(
        cfg, agents, registry, store, ops, buffer_strategy, keep_alive=persistence.keep_alive
    )

    app = FastAPI(title="GeoSearch Agent")
    app.state.cfg = cfg
    app.state.runner = runner
    app.state.catalog = catalog
    app.state.persistence = persistence  # tests close it to simulate a stopped process

    register_exception_handlers(app)
    app.include_router(router)
    return app
