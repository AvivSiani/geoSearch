"""Shared fixtures: WKT samples and config (Stage 1), MongoDB (Stage 3)."""

import uuid
from collections.abc import Iterator

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES, BufferStrategy
from geosearch.geo.ops import AreaOps
from geosearch.registry.mongo import connect, ping

HANDOFF_POLYGON = (
    "POLYGON((34.75 32.05, 34.80 32.05, 34.80 32.10, 34.75 32.10, 34.75 32.05))"
)
TLV_POINT = "POINT(34.78 32.08)"
POINT_Z = "POINT Z (34.78 32.08 10)"
C_SHAPE = "POLYGON((0 0, 10 0, 10 1, 1 1, 1 9, 10 9, 10 10, 0 10, 0 0))"
WITH_HOLE = "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0), (1 1, 3 1, 3 3, 1 3, 1 1))"
TWO_SQUARES = "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)), ((5 5, 6 5, 6 6, 5 6, 5 5)))"
BOWTIE = "POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))"
OUT_OF_RANGE = "POLYGON((200 0, 201 0, 201 1, 200 1, 200 0))"
LINE = "LINESTRING(34.75 32.05, 34.80 32.10)"
EMPTY = "POLYGON EMPTY"
BROKEN = "POLYGON((34.75 32.05, 34.80"
HUGE = "POLYGON((0 0, 2 0, 2 2, 0 2, 0 0))"


@pytest.fixture
def cfg() -> GeoConfig:
    """Default configuration, isolated from any environment overrides."""
    return GeoConfig(_env_file=None)


@pytest.fixture
def store(cfg: GeoConfig) -> InMemoryAreaStore:
    return InMemoryAreaStore(max_entries=cfg.area_store.max_entries)


@pytest.fixture
def ops(store: InMemoryAreaStore) -> AreaOps:
    return AreaOps(store)


@pytest.fixture
def buffer_strategy(cfg: GeoConfig) -> BufferStrategy:
    return STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)


MONGO_DOWN = "MongoDB not running: docker compose up -d"


@pytest.fixture(scope="session")
def mongo_client() -> Iterator[MongoClient]:
    """One client per session; skips (not fails) every MongoDB test when the
    server is down, so the Stage 1-2 suite never needs it (Stage 3 invariant 4)."""
    mongo_cfg = GeoConfig(_env_file=None).mongo.model_copy(
        update={"server_selection_timeout_ms": 500}
    )
    client = connect(mongo_cfg)
    if not ping(client):
        client.close()
        pytest.skip(MONGO_DOWN)
    yield client
    client.close()


@pytest.fixture
def mongo_db(mongo_client: MongoClient) -> Iterator[Database]:
    """A throwaway database per test, dropped afterwards."""
    name = f"geosearch_test_{uuid.uuid4().hex[:8]}"
    yield mongo_client[name]
    mongo_client.drop_database(name)
