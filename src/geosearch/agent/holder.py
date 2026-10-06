"""Holds the current agent and rebuilds it when the tool registry changes.

Why a rebuild rather than adding tools to a live agent: a compiled agent's tool
set is fixed, and registering every catalog tool up front is what gives loaded
tools native tool calling with real schemas. So a registry change (a CLI seed or
delete) means a new agent, built from the new catalog snapshot.

The rebuild happens only between requests: `current()` is called once at the
start of a request, and the request keeps the agent it got even if another
request triggers a rebuild meanwhile. All agents share one checkpointer, so a
conversation continues seamlessly on the rebuilt agent — its `loaded_tools`
carry over, and ids whose tools were deleted are simply ignored.
"""

import logging
import threading

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from geosearch.agent.build import build_agent
from geosearch.config import GeoConfig
from geosearch.registry.catalog import CatalogCache, CatalogSnapshot

log = logging.getLogger(__name__)


class AgentHolder:
    def __init__(
        self,
        cfg: GeoConfig,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver | None,
        catalog: CatalogCache | None = None,
    ):
        self._cfg = cfg
        self._model = model
        self._checkpointer = checkpointer
        self._catalog = catalog
        self._lock = threading.Lock()
        self._snapshot = catalog.snapshot if catalog else CatalogSnapshot()
        self._agent = self._build(self._snapshot)

    def _build(self, snapshot: CatalogSnapshot) -> CompiledStateGraph:
        return build_agent(self._cfg, self._model, self._checkpointer, snapshot)

    @property
    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    def current(self) -> CompiledStateGraph:
        """The agent for one request: refresh the catalog (one revision read)
        and rebuild if it changed. Call once per request and keep the result."""
        if self._catalog is None:
            return self._agent
        self._catalog.refresh_if_changed()
        snapshot = self._catalog.snapshot
        if snapshot is self._snapshot:
            return self._agent
        with self._lock:
            if snapshot is not self._snapshot:  # another request may have rebuilt already
                self._agent = self._build(snapshot)
                self._snapshot = snapshot
                log.info("agent rebuilt for registry revision %s", snapshot.revision)
            return self._agent
