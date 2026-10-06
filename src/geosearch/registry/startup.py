"""Opening the registry at app startup.

Two policies, both from config:
  - `registry.required`: unreachable MongoDB stops startup, or (when False)
    the app starts with an empty catalog and a warning. The catalog keeps
    retrying on each request's refresh, so it fills once MongoDB is back.
  - `registry.strict_startup`: a stored source_id without a handler stops
    startup, or (when False) is logged and left out of the catalog.
"""

import logging

from geosearch import sources
from geosearch.config import GeoConfig
from geosearch.registry.catalog import CatalogCache
from geosearch.registry.mongo import connect, database, ping
from geosearch.registry.seeding import check_startup
from geosearch.registry.store import MongoRegistry, Registry

log = logging.getLogger(__name__)


class RegistryUnavailable(RuntimeError):
    """MongoDB is unreachable and `registry.required` is set."""


def open_catalog(cfg: GeoConfig, registry: Registry | None = None) -> CatalogCache:
    """Load the handler allowlist, connect (unless a registry is injected, as
    the Mongo-free tests do), run the startup check, and build the catalog."""
    sources.load_all()
    if registry is None:
        client = connect(cfg.mongo)
        registry = MongoRegistry(database(client, cfg.mongo))
        if not ping(client):
            if cfg.registry.required:
                raise RegistryUnavailable(
                    # The URI is not echoed: it may carry credentials.
                    "MongoDB is unreachable at the configured mongo.uri "
                    "(registry.required=true). Start it with: docker compose up -d"
                )
            log.warning("registry: MongoDB unreachable; starting with an empty catalog")
            return CatalogCache(registry)

    check_startup(registry, strict=cfg.registry.strict_startup)
    catalog = CatalogCache(registry)
    catalog.refresh_if_changed()
    return catalog
