"""Where conversations persist (Stage 6): the one place that picks a backend.

`open_persistence(cfg)` returns the checkpointer and the area store the app
runs on, chosen by `conversation.store`:

  - `memory`: InMemorySaver and the in-process LRU. Unit tests and quick dev
    runs; nothing survives a restart.
  - `mongodb`: everything in `mongo.database`, so a conversation survives a
    restart. MongoDB must be reachable at startup; there is never a silent
    fallback to memory.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from geosearch.config import GeoConfig
from geosearch.geo.area_store import AreaStore, InMemoryAreaStore


class PersistenceUnavailable(RuntimeError):
    """`conversation.store=mongodb` and MongoDB is unreachable at startup."""


@dataclass
class Persistence:
    checkpointer: BaseCheckpointSaver
    area_store: AreaStore
    close: Callable[[], None] = field(default=lambda: None)


def open_persistence(cfg: GeoConfig) -> Persistence:
    if cfg.conversation.store == "memory":
        return Persistence(
            checkpointer=InMemorySaver(),
            area_store=InMemoryAreaStore(max_entries=cfg.area_store.max_entries),
        )
    raise NotImplementedError("Stage 6 step 5: mongodb persistence")
