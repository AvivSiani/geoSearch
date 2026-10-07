"""MongoAreaStore (Stage 6): write-through, read-through, exact geometry, expiry.
Real MongoDB; skips when it's down."""

from datetime import UTC, datetime, timedelta

import pytest
import shapely
from conftest import HANDOFF_POLYGON, TWO_SQUARES
from pymongo.database import Database

from geosearch.geo.area_store import AreaNotFound, MongoAreaStore, make_area_id

TTL = timedelta(days=7)
PRECISE = (
    "POLYGON((34.7500004 32.0500007, 34.8000003 32.05, 34.8 32.1000006, "
    "34.75 32.1, 34.7500004 32.0500007))"
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


def _store(db: Database, clock: Clock, max_entries: int = 10) -> MongoAreaStore:
    store = MongoAreaStore(db["areas"], max_entries=max_entries, ttl=TTL, clock=clock)
    store.ensure_indexes()
    return store


def _expires(db: Database, area_id: str) -> datetime:
    return db["areas"].find_one({"_id": area_id})["expires_at"].replace(tzinfo=UTC)


def test_put_writes_through_and_is_idempotent(mongo_db: Database) -> None:
    clock = Clock()
    store = _store(mongo_db, clock)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    assert store.put(shapely.from_wkt(HANDOFF_POLYGON)) == area_id
    assert mongo_db["areas"].count_documents({}) == 1
    doc = mongo_db["areas"].find_one({"_id": area_id})
    assert doc["geometry_type"] == "Polygon"
    assert _expires(mongo_db, area_id) == clock.now + TTL


@pytest.mark.parametrize("wkt", [HANDOFF_POLYGON, PRECISE, TWO_SQUARES])
def test_a_fresh_store_reads_the_exact_area_back(mongo_db: Database, wkt: str) -> None:
    clock = Clock()
    area_id = _store(mongo_db, clock).put(shapely.from_wkt(wkt))
    restarted = _store(mongo_db, clock)  # empty LRU, as after a restart
    area = restarted.get(area_id)
    assert shapely.equals_exact(area, shapely.normalize(shapely.from_wkt(wkt)), tolerance=0)
    assert make_area_id(area) == area_id
    assert area_id in restarted._cache  # cached after the first read


def test_unknown_and_expired_areas_are_not_found(mongo_db: Database) -> None:
    clock = Clock()
    area_id = _store(mongo_db, clock).put(shapely.from_wkt(HANDOFF_POLYGON))
    restarted = _store(mongo_db, clock)
    with pytest.raises(AreaNotFound):
        restarted.get("area_000000000000")
    clock.now += TTL + timedelta(seconds=1)  # past expires_at, before the TTL sweep
    with pytest.raises(AreaNotFound):
        restarted.get(area_id)


def test_touch_only_moves_expiry_forward(mongo_db: Database) -> None:
    clock = Clock()
    store = _store(mongo_db, clock)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    first = _expires(mongo_db, area_id)
    clock.now += timedelta(days=2)
    store.touch(area_id)
    assert _expires(mongo_db, area_id) == first + timedelta(days=2)
    clock.now -= timedelta(days=5)  # a slower clock elsewhere never shortens it
    store.touch(area_id)
    assert _expires(mongo_db, area_id) == first + timedelta(days=2)


def test_touch_rewrites_a_swept_area_still_in_the_cache(mongo_db: Database) -> None:
    clock = Clock()
    store = _store(mongo_db, clock)
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    mongo_db["areas"].delete_many({})  # the TTL monitor got there first
    store.touch(area_id)
    assert _store(mongo_db, clock).get(area_id).equals(shapely.from_wkt(HANDOFF_POLYGON))


def test_an_evicted_area_is_read_back(mongo_db: Database) -> None:
    clock = Clock()
    store = _store(mongo_db, clock, max_entries=1)
    first = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    store.put(shapely.from_wkt(TWO_SQUARES))  # evicts the first from the LRU
    assert first not in store._cache
    assert store.get(first).equals(shapely.from_wkt(HANDOFF_POLYGON))


def test_ttl_index(mongo_db: Database) -> None:
    _store(mongo_db, Clock())
    _store(mongo_db, Clock())
    ttl = [i for i in mongo_db["areas"].list_indexes() if "expireAfterSeconds" in i]
    assert [(dict(i["key"]), i["expireAfterSeconds"]) for i in ttl] == [({"expires_at": 1}, 0)]
