"""Stage 3 step 7: CatalogCache — rebuild on revision change only, keep the
last good catalog on failure, expose what Stage 4's disclosure will need."""

from typing import Any

import pytest
from pydantic import BaseModel
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError

from geosearch.registry.catalog import CatalogCache
from geosearch.registry.handlers import HandlerRegistry, ToolResult, UnknownHandler
from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import InMemoryRegistry, MongoRegistry


class In(BaseModel):
    q: str


class Row(BaseModel):
    name: str
    score: float


HANDLERS = HandlerRegistry()
for _sid in ("places.search", "places.near", "weather.now"):
    HANDLERS.register(_sid, input_model=In, output_model=Row)(
        lambda args, ctx: ToolResult(summary="ok")
    )


def td(source_id: str, description: str = "Does it.") -> ToolDefinition:
    return ToolDefinition(source_id=source_id, description=description)


class CountingRegistry(InMemoryRegistry):
    """Counts reads, and can be told to fail like a dead MongoDB."""

    def __init__(self, tools: list[ToolDefinition] | None = None):
        super().__init__(tools)
        self.calls: dict[str, int] = {"revision": 0, "get_tools": 0}
        self.down = False

    def revision(self) -> int:
        self.calls["revision"] += 1
        if self.down:
            raise ServerSelectionTimeoutError("down")
        return super().revision()

    def get_tools(self, capability: str) -> list[ToolDefinition]:
        self.calls["get_tools"] += 1
        return super().get_tools(capability)


def _catalog(registry: Any) -> CatalogCache:
    return CatalogCache(registry, handler_registry=HANDLERS)


def test_first_refresh_builds_then_unchanged_revision_is_one_read() -> None:
    registry = CountingRegistry()
    registry.upsert_tool(td("places.search"))
    catalog = _catalog(registry)
    assert catalog.capabilities == [] and catalog.revision is None

    assert catalog.refresh_if_changed() is True
    assert catalog.capabilities == ["places"]
    reads = dict(registry.calls)

    assert catalog.refresh_if_changed() is False
    assert registry.calls["revision"] == reads["revision"] + 1
    assert registry.calls["get_tools"] == reads["get_tools"]  # no rebuild


def test_revision_change_triggers_rebuild() -> None:
    registry = InMemoryRegistry()
    registry.upsert_tool(td("places.search", "Find places."))
    catalog = _catalog(registry)
    catalog.refresh_if_changed()

    registry.upsert_tool(td("weather.now", "Current weather."))
    registry.upsert_tool(td("places.search", "Find places, v2."))
    assert catalog.refresh_if_changed() is True
    assert catalog.capabilities == ["places", "weather"]
    assert catalog.descriptions("places") == [("places_search", "Find places, v2.")]

    registry.delete_tool("weather.now")
    catalog.refresh_if_changed()
    assert catalog.capabilities == ["places"]
    assert catalog.tools_for("weather") == []


def test_exposed_views() -> None:
    registry = InMemoryRegistry()
    for t in (td("places.search", "Search."), td("places.near", "Near.")):
        registry.upsert_tool(t)
    catalog = _catalog(registry)
    catalog.refresh_if_changed()

    assert [t.name for t in catalog.tools_for("places")] == ["places_near", "places_search"]
    assert catalog.descriptions("places") == [
        ("places_near", "Near."),
        ("places_search", "Search."),
    ]
    assert catalog.output_fields("places.search") == ["name", "score"]
    with pytest.raises(UnknownHandler):
        catalog.output_fields("nope.tool")


def test_tools_without_a_handler_are_left_out() -> None:
    registry = InMemoryRegistry()
    registry.upsert_tool(td("places.search"))
    registry.upsert_tool(td("rogue.tool"))
    catalog = _catalog(registry)
    catalog.refresh_if_changed()
    assert catalog.capabilities == ["places"]


def test_failure_keeps_last_good_catalog(caplog: pytest.LogCaptureFixture) -> None:
    registry = CountingRegistry()
    registry.upsert_tool(td("places.search"))
    catalog = _catalog(registry)
    catalog.refresh_if_changed()

    registry.down = True
    registry.upsert_tool(td("weather.now"))
    assert catalog.refresh_if_changed() is False
    assert catalog.capabilities == ["places"]
    assert "keeping the last good catalog" in caplog.text

    registry.down = False
    assert catalog.refresh_if_changed() is True
    assert catalog.capabilities == ["places", "weather"]


def test_rebuild_follows_mongo_revision(mongo_db: Database) -> None:
    writer = MongoRegistry(mongo_db)
    catalog = _catalog(MongoRegistry(mongo_db))
    catalog.refresh_if_changed()
    assert catalog.capabilities == []

    writer.upsert_tool(td("places.search"))
    assert catalog.refresh_if_changed() is True
    assert catalog.capabilities == ["places"]
    assert catalog.refresh_if_changed() is False
