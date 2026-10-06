"""Registry storage: tool definitions plus a revision counter.

Two collections:
  - `tools`: one document per tool, `{_id: <source_id int>, description}` and
    nothing else. There is no grouping, so the only queries are "all tools"
    (sorted by id) and "one tool by id".
  - `registry_meta`: one document `{_id: "registry", revision, updated_at}`.

Why a revision counter: the app checks it once per request (one tiny read)
and rebuilds its tool catalog only when it changed, instead of re-reading every
definition on every request. Every real change bumps it; a no-op does not.
"""

import threading
from datetime import UTC, datetime
from typing import Protocol

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from geosearch.registry.models import ToolDefinition

META_ID = "registry"


class ToolNotFound(Exception):
    def __init__(self, source_id: int):
        super().__init__(f"tool {source_id} is not in the registry")
        self.source_id = source_id


class Registry(Protocol):
    def revision(self) -> int: ...
    def list_tools(self) -> list[ToolDefinition]: ...  # sorted by source_id
    def get_tool(self, source_id: int) -> ToolDefinition: ...
    def upsert_tool(self, td: ToolDefinition) -> bool: ...
    def delete_tool(self, source_id: int) -> bool: ...


class MongoRegistry:
    """The production registry, on a PyMongo `Database`."""

    def __init__(self, db: Database):
        self._tools = db["tools"]
        self._meta = db["registry_meta"]

    def revision(self) -> int:
        doc = self._meta.find_one({"_id": META_ID}, {"revision": 1})
        return int(doc["revision"]) if doc else 0

    def list_tools(self) -> list[ToolDefinition]:
        return [_to_definition(doc) for doc in self._tools.find({}).sort("_id", 1)]

    def get_tool(self, source_id: int) -> ToolDefinition:
        doc = self._tools.find_one({"_id": source_id})
        if doc is None:
            raise ToolNotFound(source_id)
        return _to_definition(doc)

    def upsert_tool(self, td: ToolDefinition) -> bool:
        """Writes only if the description differs, atomically: the filter matches
        an existing document with a *different* description; if the document
        exists with the same description nothing matches, the upsert tries to
        insert a duplicate `_id`, and that DuplicateKeyError means "unchanged"."""
        try:
            self._tools.update_one(
                {"_id": td.source_id, "description": {"$ne": td.description}},
                {"$set": {"description": td.description}},
                upsert=True,
            )
        except DuplicateKeyError:
            return False
        self._bump()
        return True

    def delete_tool(self, source_id: int) -> bool:
        if self._tools.delete_one({"_id": source_id}).deleted_count == 0:
            return False
        self._bump()
        return True

    def _bump(self) -> int:
        doc = self._meta.find_one_and_update(
            {"_id": META_ID},
            {"$inc": {"revision": 1}, "$set": {"updated_at": datetime.now(UTC)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["revision"])


def _to_definition(doc: dict) -> ToolDefinition:
    return ToolDefinition(source_id=doc["_id"], description=doc["description"])


class InMemoryRegistry:
    """Same contract without MongoDB: used by tests that exercise the resolver,
    catalog, agent and app wiring, and by the Stage 1-2 suite (invariant 16)."""

    def __init__(self, tools: list[ToolDefinition] | None = None):
        self._lock = threading.Lock()
        self._tools: dict[int, ToolDefinition] = {td.source_id: td for td in tools or []}
        self._revision = 0

    def revision(self) -> int:
        return self._revision

    def list_tools(self) -> list[ToolDefinition]:
        return [self._tools[k] for k in sorted(self._tools)]

    def get_tool(self, source_id: int) -> ToolDefinition:
        try:
            return self._tools[source_id]
        except KeyError:
            raise ToolNotFound(source_id) from None

    def upsert_tool(self, td: ToolDefinition) -> bool:
        with self._lock:
            if self._tools.get(td.source_id) == td:
                return False
            self._tools[td.source_id] = td
            self._revision += 1
            return True

    def delete_tool(self, source_id: int) -> bool:
        with self._lock:
            if self._tools.pop(source_id, None) is None:
                return False
            self._revision += 1
            return True
