"""Stage 3 step 4: the registry store contract, run against MongoDB and the
in-memory implementation, plus MongoDB-specific storage checks."""

import pytest
from pymongo.database import Database

from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import InMemoryRegistry, MongoRegistry, Registry, ToolNotFound


def td(source_id: str, description: str = "Does it.") -> ToolDefinition:
    return ToolDefinition(source_id=source_id, description=description)


@pytest.fixture(params=["mongo", "memory"])
def registry(request: pytest.FixtureRequest) -> Registry:
    if request.param == "memory":
        return InMemoryRegistry()
    return MongoRegistry(request.getfixturevalue("mongo_db"))


def test_empty_registry(registry: Registry) -> None:
    assert registry.revision() == 0
    assert registry.list_capabilities() == []
    assert registry.get_tools("demo") == []


def test_crud_roundtrip(registry: Registry) -> None:
    assert registry.upsert_tool(td("demo.a", "first")) is True
    assert registry.get_tool("demo.a") == td("demo.a", "first")
    assert registry.upsert_tool(td("demo.a", "second")) is True
    assert registry.get_tool("demo.a").description == "second"
    assert registry.delete_tool("demo.a") is True
    with pytest.raises(ToolNotFound, match="demo.a"):
        registry.get_tool("demo.a")
    assert registry.delete_tool("demo.a") is False


def test_prefix_lookup_is_exact_and_sorted(registry: Registry) -> None:
    for source_id in ("demo.b", "demo.a", "demox.c", "places.d", "dem.e"):
        registry.upsert_tool(td(source_id))
    assert [t.source_id for t in registry.get_tools("demo")] == ["demo.a", "demo.b"]
    assert registry.get_tools("dem") == [td("dem.e")]
    assert registry.list_capabilities() == ["dem", "demo", "demox", "places"]


def test_idempotent_upsert_does_not_bump_revision(registry: Registry) -> None:
    registry.upsert_tool(td("demo.a"))
    rev = registry.revision()
    assert registry.upsert_tool(td("demo.a")) is False
    assert registry.revision() == rev


def test_every_change_bumps_revision(registry: Registry) -> None:
    registry.upsert_tool(td("demo.a"))
    assert registry.revision() == 1
    registry.upsert_tool(td("demo.a", "changed"))
    assert registry.revision() == 2
    registry.delete_tool("demo.a")
    assert registry.revision() == 3
    registry.delete_tool("demo.a")  # no-op
    assert registry.revision() == 3


# --- MongoDB-specific ----------------------------------------------------------


def test_stored_documents_hold_only_source_id_and_description(mongo_db: Database) -> None:
    MongoRegistry(mongo_db).upsert_tool(td("demo.a"))
    stored = mongo_db.tools.find_one({"_id": "demo.a"})
    assert stored == {"_id": "demo.a", "description": "Does it."}


def test_meta_document_shape(mongo_db: Database) -> None:
    MongoRegistry(mongo_db).upsert_tool(td("demo.a"))
    meta = mongo_db.registry_meta.find_one({"_id": "registry"})
    assert meta is not None
    assert set(meta) == {"_id", "revision", "updated_at"}
    assert meta["revision"] == 1


def test_prefix_query_uses_the_id_index(mongo_db: Database) -> None:
    reg = MongoRegistry(mongo_db)
    for i in range(5):
        reg.upsert_tool(td(f"demo.t{i}"))
        reg.upsert_tool(td(f"other.t{i}"))
    plan = mongo_db.tools.find({"_id": {"$regex": "^demo\\."}}).explain()
    winning = str(plan["queryPlanner"]["winningPlan"])
    assert "IXSCAN" in winning and "COLLSCAN" not in winning


def test_regex_metacharacters_in_capability_are_escaped(mongo_db: Database) -> None:
    reg = MongoRegistry(mongo_db)
    reg.upsert_tool(td("demo.a"))
    assert reg.get_tools(".*") == []


def test_two_registries_on_one_db_share_state(mongo_db: Database) -> None:
    writer, reader = MongoRegistry(mongo_db), MongoRegistry(mongo_db)
    writer.upsert_tool(td("demo.a"))
    assert reader.revision() == 1
    assert reader.get_tool("demo.a") == td("demo.a")
