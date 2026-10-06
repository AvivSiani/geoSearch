"""CatalogCache — rebuild on revision change only, keep the last good catalog
on failure, expose what progressive disclosure needs."""

from typing import Any

import pytest
from pydantic import BaseModel
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError

from geosearch.registry.catalog import CatalogCache, CatalogEntry
from geosearch.registry.handlers import HandlerRegistry, ToolResult, UnknownHandler
from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import InMemoryRegistry, MongoRegistry, ToolNotFound


class In(BaseModel):
    q: str


class Row(BaseModel):
    name: str
    score: float


HANDLERS = HandlerRegistry()
for _sid in (17, 18, 103):
    HANDLERS.register(source_id=_sid, input_model=In, output_model=Row)(
        lambda args, ctx: ToolResult(summary="ok")
    )


def td(source_id: int, description: str = "Does it.") -> ToolDefinition:
    return ToolDefinition(source_id=source_id, description=description)


class CountingRegistry(InMemoryRegistry):
    """Counts reads, and can be told to fail like a dead MongoDB."""

    def __init__(self, tools: list[ToolDefinition] | None = None):
        super().__init__(tools)
        self.calls: dict[str, int] = {"revision": 0, "list_tools": 0}
        self.down = False

    def revision(self) -> int:
        self.calls["revision"] += 1
        if self.down:
            raise ServerSelectionTimeoutError("down")
        return super().revision()

    def list_tools(self) -> list[ToolDefinition]:
        self.calls["list_tools"] += 1
        return super().list_tools()


def _catalog(registry: Any) -> CatalogCache:
    return CatalogCache(registry, handler_registry=HANDLERS)


def test_first_refresh_builds_then_unchanged_revision_is_one_read() -> None:
    registry = CountingRegistry()
    registry.upsert_tool(td(17))
    catalog = _catalog(registry)
    assert catalog.tools == () and catalog.revision is None

    assert catalog.refresh_if_changed() is True
    assert [e.source_id for e in catalog.tools] == [17]
    reads = dict(registry.calls)

    assert catalog.refresh_if_changed() is False
    assert registry.calls["revision"] == reads["revision"] + 1
    assert registry.calls["list_tools"] == reads["list_tools"]  # no rebuild


def test_revision_change_triggers_rebuild() -> None:
    registry = InMemoryRegistry()
    registry.upsert_tool(td(17, "Find places."))
    catalog = _catalog(registry)
    catalog.refresh_if_changed()

    registry.upsert_tool(td(103, "Current weather."))
    registry.upsert_tool(td(17, "Find places, v2."))
    assert catalog.refresh_if_changed() is True
    assert catalog.tools == (
        CatalogEntry(17, "source_17", "Find places, v2."),
        CatalogEntry(103, "source_103", "Current weather."),
    )

    registry.delete_tool(103)
    catalog.refresh_if_changed()
    assert [e.source_id for e in catalog.tools] == [17]
    with pytest.raises(ToolNotFound):
        catalog.tool(103)


def test_exposed_views() -> None:
    registry = InMemoryRegistry([td(103, "Weather."), td(17, "Search."), td(18, "Near.")])
    catalog = _catalog(registry)
    catalog.refresh_if_changed()

    assert [e.source_id for e in catalog.tools] == [17, 18, 103]  # sorted by id
    assert catalog.tool(18).name == "source_18"
    assert catalog.tool(18).description == "Near."
    assert catalog.source_id_of("source_103") == 103
    assert catalog.source_id_of("geo_describe_area") is None
    assert catalog.output_fields(17) == ["name", "score"]
    with pytest.raises(UnknownHandler):
        catalog.output_fields(99)


def test_snapshots_are_immutable_views() -> None:
    registry = InMemoryRegistry([td(17)])
    catalog = _catalog(registry)
    catalog.refresh_if_changed()
    before = catalog.snapshot

    registry.upsert_tool(td(18))
    catalog.refresh_if_changed()
    assert [e.source_id for e in before.tools] == [17]  # the old snapshot is untouched
    assert 18 not in before and 18 in catalog.snapshot
    assert [t.name for t in catalog.snapshot.resolved_tools()] == ["source_17", "source_18"]


def test_tools_without_a_handler_are_left_out() -> None:
    catalog = _catalog(InMemoryRegistry([td(17), td(666)]))
    catalog.refresh_if_changed()
    assert [e.source_id for e in catalog.tools] == [17]


def test_failure_keeps_last_good_catalog(caplog: pytest.LogCaptureFixture) -> None:
    registry = CountingRegistry()
    registry.upsert_tool(td(17))
    catalog = _catalog(registry)
    catalog.refresh_if_changed()

    registry.down = True
    registry.upsert_tool(td(103))
    assert catalog.refresh_if_changed() is False
    assert [e.source_id for e in catalog.tools] == [17]
    assert "keeping the last good catalog" in caplog.text

    registry.down = False
    assert catalog.refresh_if_changed() is True
    assert [e.source_id for e in catalog.tools] == [17, 103]


def test_rebuild_follows_mongo_revision(mongo_db: Database) -> None:
    writer = MongoRegistry(mongo_db)
    catalog = _catalog(MongoRegistry(mongo_db))
    catalog.refresh_if_changed()
    assert catalog.tools == ()

    writer.upsert_tool(td(17))
    assert catalog.refresh_if_changed() is True
    assert [e.source_id for e in catalog.tools] == [17]
    assert catalog.refresh_if_changed() is False
