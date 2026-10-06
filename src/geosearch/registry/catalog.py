"""The app's resolved view of the registry, rebuilt only when it changes.

Why a cache keyed on the revision: resolving tools is cheap but not free, and
the registry changes rarely (a CLI seed, a delete). One small revision read per
request tells us whether anything changed; only then do we re-read and
re-resolve. If MongoDB fails, the last good catalog keeps serving.

Each rebuild produces an immutable `CatalogSnapshot`. The agent is built from
one snapshot and keeps it, so an in-flight request never sees a catalog that
doesn't match the tools its agent registered (Stage 4).

What a snapshot exposes is shaped for progressive disclosure:
  - `tools`: `(source_id, model_name, description)` entries, sorted by id —
    the always-on catalog;
  - `output_fields(source_id)`: the returned fields, shown once a tool is loaded;
  - `tool(source_id)` / `source_id_of(model_name)`: the resolved tools.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from langchain_core.tools import BaseTool

from geosearch.registry.handlers import HandlerRegistry, handlers
from geosearch.registry.models import ToolDefinition, model_name_for
from geosearch.registry.resolver import resolve
from geosearch.registry.store import Registry, ToolNotFound

log = logging.getLogger(__name__)

Resolver = Callable[[ToolDefinition], BaseTool]


class CatalogEntry(NamedTuple):
    source_id: int
    model_name: str
    description: str


@dataclass(frozen=True)
class CatalogSnapshot:
    """One revision's catalog. Never mutated; a rebuild makes a new one."""

    revision: int | None = None
    tools: tuple[CatalogEntry, ...] = ()
    handler_registry: HandlerRegistry = field(default=handlers, repr=False)
    _resolved: dict[int, BaseTool] = field(default_factory=dict, repr=False)

    def tool(self, source_id: int) -> BaseTool:
        try:
            return self._resolved[source_id]
        except KeyError:
            raise ToolNotFound(source_id) from None

    def resolved_tools(self) -> list[BaseTool]:
        return [self._resolved[entry.source_id] for entry in self.tools]

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._resolved

    def source_id_of(self, model_name: str) -> int | None:
        """The registry id behind a model-facing tool name, or None for a core
        tool (or a registry tool no longer in this catalog)."""
        for entry in self.tools:
            if entry.model_name == model_name:
                return entry.source_id
        return None

    def output_fields(self, source_id: int) -> list[str]:
        """Raises UnknownHandler for a source_id outside the allowlist."""
        return self.handler_registry.get(source_id).output_fields


class CatalogCache:
    def __init__(
        self,
        registry: Registry,
        resolver: Resolver | None = None,
        handler_registry: HandlerRegistry = handlers,
    ):
        self._registry = registry
        self._handlers = handler_registry
        self._resolve = resolver or (lambda td: resolve(td, handler_registry))
        self._lock = threading.Lock()
        self._snapshot = CatalogSnapshot(handler_registry=handler_registry)

    def refresh_if_changed(self) -> bool:
        """One revision read; a rebuild only if it moved. Returns True if rebuilt.
        Any failure (MongoDB down, a malformed stored document) is logged and the
        last good catalog is kept."""
        try:
            # Outside the lock, so a slow or dead server doesn't serialize requests.
            revision = self._registry.revision()
            if revision == self._snapshot.revision:
                return False
            with self._lock:  # one rebuild at a time
                if revision == self._snapshot.revision:  # another request just rebuilt
                    return False
                # The revision is read before the tools: if a write lands mid-rebuild,
                # the stored revision is stale and the next request rebuilds again.
                self._snapshot = self._build(revision)
        except Exception:
            log.warning("registry: refresh failed; keeping the last good catalog", exc_info=True)
            return False
        log.info("registry: catalog rebuilt at revision %s", revision)
        return True

    def _build(self, revision: int) -> CatalogSnapshot:
        entries: list[CatalogEntry] = []
        resolved: dict[int, BaseTool] = {}
        for td in self._registry.list_tools():
            if td.source_id not in self._handlers:
                log.warning("registry: skipping source_id %s: no handler registered", td.source_id)
                continue
            entries.append(CatalogEntry(td.source_id, model_name_for(td.source_id), td.description))
            resolved[td.source_id] = self._resolve(td)
        entries.sort()
        return CatalogSnapshot(revision, tuple(entries), self._handlers, resolved)

    @property
    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    @property
    def revision(self) -> int | None:
        return self._snapshot.revision

    @property
    def tools(self) -> tuple[CatalogEntry, ...]:
        return self._snapshot.tools

    def tool(self, source_id: int) -> BaseTool:
        return self._snapshot.tool(source_id)

    def source_id_of(self, model_name: str) -> int | None:
        return self._snapshot.source_id_of(model_name)

    def output_fields(self, source_id: int) -> list[str]:
        return self._snapshot.output_fields(source_id)
