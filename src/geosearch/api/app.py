"""The app factory. request/ and geo/ hold the deterministic logic; agent/ holds
the agent; this module wires them together once and exposes the route.

The model, checkpointer, registry and agent are built once at startup and kept on
app.state. A model instance can be injected (tests pass a scripted model; nothing
here imports a provider class — agent/model.py owns that)."""

from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel

from geosearch.agent.build import build_agent
from geosearch.agent.conversations import ConversationRegistry, make_checkpointer
from geosearch.agent.model import build_chat_model
from geosearch.agent.run import RequestRunner
from geosearch.api.errors import register_exception_handlers
from geosearch.api.routes import router
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps


def create_app(cfg: GeoConfig | None = None, model: BaseChatModel | None = None) -> FastAPI:
    cfg = cfg or GeoConfig()
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    buffer_strategy = STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)

    model = model or build_chat_model(cfg.llm, cfg.budget)
    checkpointer = make_checkpointer(cfg)
    registry = ConversationRegistry(cfg.conversation, checkpointer)
    agent = build_agent(cfg, model, checkpointer)
    runner = RequestRunner(cfg, agent, registry, store, ops, buffer_strategy)

    app = FastAPI(title="GeoSearch Agent")
    app.state.cfg = cfg
    app.state.runner = runner

    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
