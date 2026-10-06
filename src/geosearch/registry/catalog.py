"""The app's resolved view of the registry, rebuilt only when it changes.

Why a cache keyed on the revision: resolving tools is cheap but not free, and
the registry changes rarely (a CLI seed, a delete). One small revision read per
request tells us whether anything changed; only then do we re-read and
re-resolve. If MongoDB fails, the last good catalog keeps serving.

What it exposes is shaped for Stage 4's progressive disclosure:
  - `descriptions(capability)`: the always-on catalog (names and descriptions);
  - `output_fields(source_id)`: the returned fields, shown once a capability
    is loaded;
  - `tools_for(capability)`: the resolved tools themselves.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from geosearch.registry.handlers import HandlerRegistry, handlers
from geosearch.registry.models import ToolDefinition
from geosearch.registry.resolver import resolve
from geosearch.registry.store import Registry

log = logging.getLogger(__name__)

Resolver = Callable[[ToolDefinition], BaseTool]


@dataclass(frozen=True)
class _Snapshot:
    revision: int | None = None
    definitions: dict[str, list[ToolDefinition]] = field(default_factory=dict)
    tools: dict[str, list[BaseTool]] = field(default_factory=dict)


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
        self._snapshot = _Snapshot()  # replaced whole, so readers never see a mix

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

    def _build(self, revision: int) -> _Snapshot:
        definitions: dict[str, list[ToolDefinition]] = {}
        tools: dict[str, list[BaseTool]] = {}
        for capability in self._registry.list_capabilities():
            allowed = []
            for td in self._registry.get_tools(capability):
                if td.source_id in self._handlers:
                    allowed.append(td)
                else:
                    log.warning("registry: skipping %r: no handler registered", td.source_id)
            if allowed:
                definitions[capability] = allowed
                tools[capability] = [self._resolve(td) for td in allowed]
        return _Snapshot(revision, definitions, tools)

    @property
    def revision(self) -> int | None:
        return self._snapshot.revision

    @property
    def capabilities(self) -> list[str]:
        return sorted(self._snapshot.definitions)

    def tools_for(self, capability: str) -> list[BaseTool]:
        return list(self._snapshot.tools.get(capability, []))

    def descriptions(self, capability: str) -> list[tuple[str, str]]:
        return [
            (td.model_name, td.description)
            for td in self._snapshot.definitions.get(capability, [])
        ]

    def output_fields(self, source_id: str) -> list[str]:
        """Raises UnknownHandler for a source_id outside the allowlist."""
        return self._handlers.get(source_id).output_fields
