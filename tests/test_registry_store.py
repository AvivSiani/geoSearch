"""The registry store contract, run against MongoDB and the in-memory
implementation, plus MongoDB-specific storage checks."""

import pytest
from pymongo.database import Database

from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import InMemoryRegistry, MongoRegistry, Registry, ToolNotFound


def td(source_id: int, description: str = "Does it.") -> ToolDefinition:
    return ToolDefinition(source_id=source_id, description=description)


@pytest.fixture(params=["mongo", "memory"])
def registry(request: pytest.FixtureRequest) -> Registry:
    if request.param == "memory":
        return InMemoryRegistry()
    return MongoRegistry(request.getfixturevalue("mongo_db"))


def test_empty_registry(registry: Registry) -> None:
    assert registry.revision() == 0
    assert registry.list_tools() == []


def test_crud_roundtrip(registry: Registry) -> None:
    assert registry.upsert_tool(td(17, "first")) is True
    assert registry.get_tool(17) == td(17, "first")
    assert registry.upsert_tool(td(17, "second")) is True
    assert registry.get_tool(17).description == "second"
    assert registry.delete_tool(17) is True
    with pytest.raises(ToolNotFound, match="17"):
        registry.get_tool(17)
    assert registry.delete_tool(17) is False


def test_list_tools_is_sorted_by_id(registry: Registry) -> None:
    for source_id in (9001, 17, 103, 2):
        registry.upsert_tool(td(source_id))
    assert [t.source_id for t in registry.list_tools()] == [2, 17, 103, 9001]


def test_idempotent_upsert_does_not_bump_revision(registry: Registry) -> None:
    registry.upsert_tool(td(17))
    rev = registry.revision()
    assert registry.upsert_tool(td(17)) is False
    assert registry.revision() == rev


def test_every_change_bumps_revision(registry: Registry) -> None:
    registry.upsert_tool(td(17))
    assert registry.revision() == 1
    registry.upsert_tool(td(17, "changed"))
    assert registry.revision() == 2
    registry.delete_tool(17)
    assert registry.revision() == 3
    registry.delete_tool(17)  # no-op
    assert registry.revision() == 3


# --- MongoDB-specific ----------------------------------------------------------


def test_stored_documents_hold_only_int_id_and_description(mongo_db: Database) -> None:
    MongoRegistry(mongo_db).upsert_tool(td(9001))
    assert mongo_db.tools.find_one({"_id": 9001}) == {"_id": 9001, "description": "Does it."}


def test_meta_document_shape(mongo_db: Database) -> None:
    MongoRegistry(mongo_db).upsert_tool(td(17))
    meta = mongo_db.registry_meta.find_one({"_id": "registry"})
    assert meta is not None
    assert set(meta) == {"_id", "revision", "updated_at"}
    assert meta["revision"] == 1


def test_two_registries_on_one_db_share_state(mongo_db: Database) -> None:
    writer, reader = MongoRegistry(mongo_db), MongoRegistry(mongo_db)
    writer.upsert_tool(td(17))
    assert reader.revision() == 1
    assert reader.get_tool(17) == td(17)
