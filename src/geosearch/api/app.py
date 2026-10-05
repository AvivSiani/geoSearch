"""The app factory. request/ and geo/ hold the logic; this module just wires
config, store, ops and buffer strategy onto app.state and exposes the route.
"""

from fastapi import FastAPI

from geosearch.api.errors import register_exception_handlers
from geosearch.api.routes import router
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps


def create_app(cfg: GeoConfig | None = None) -> FastAPI:
    cfg = cfg or GeoConfig()
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    buffer_strategy = STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)

    app = FastAPI(title="GeoSearch Agent")
    app.state.cfg = cfg
    app.state.store = store
    app.state.ops = ops
    app.state.buffer_strategy = buffer_strategy

    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
