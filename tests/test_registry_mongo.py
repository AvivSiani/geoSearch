"""Stage 3 step 1: the MongoDB helpers and the per-test database fixture."""

from pymongo.database import Database

from geosearch.config import MongoConfig
from geosearch.registry.mongo import connect, ping


def test_ping_fails_fast_on_unreachable_server() -> None:
    # Port 1 is never a MongoDB server; the short timeout keeps this test quick.
    client = connect(MongoConfig(uri="mongodb://localhost:1", server_selection_timeout_ms=100))
    try:
        assert ping(client) is False
    finally:
        client.close()


def test_mongo_db_is_isolated_and_writable(mongo_db: Database) -> None:
    assert mongo_db.name.startswith("geosearch_test_")
    mongo_db.things.insert_one({"_id": "a"})
    assert mongo_db.things.count_documents({}) == 1

