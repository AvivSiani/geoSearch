"""MongoDB connection helpers for the registry.

Why a fast ping: PyMongo connects lazily, so a dead server only shows up on the
first real operation. Startup and the test fixtures ping once, bounded by
`server_selection_timeout_ms`, to fail (or skip) quickly and clearly instead.
"""

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from geosearch.config import MongoConfig


def connect(cfg: MongoConfig) -> MongoClient:
    """A client whose operations give up after the configured selection timeout."""
    return MongoClient(cfg.uri, serverSelectionTimeoutMS=cfg.server_selection_timeout_ms)


def ping(client: MongoClient) -> bool:
    """True if the server answers within the client's selection timeout."""
    try:
        client.admin.command("ping")
    except PyMongoError:
        return False
    return True


def database(client: MongoClient, cfg: MongoConfig) -> Database:
    return client[cfg.database]
