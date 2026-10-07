"""Where conversations persist (Stage 6): the one place that picks a backend.

`open_persistence(cfg)` returns the checkpointer, conversation-record store and
area store the app runs on, chosen by `conversation.store`:

  - `memory`: InMemorySaver, an in-memory record store and the in-process LRU.
    Unit tests and quick dev runs; nothing survives a restart.
  - `mongodb`: everything in `mongo.database`, so a conversation survives a
    restart. MongoDB must be reachable at startup; there is never a silent
    fallback to memory.

`keep_alive(conversation_id, area_id)` runs after every finished turn and
pushes out the expiry of what the conversation depends on: all of its
checkpoints (a DeltaChannel chain must expire whole) and its area.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from geosearch.agent.conversations import (
    ConversationStore,
    InMemoryConversationStore,
    MongoConversationStore,
)
from geosearch.agent.mongo_saver import MongoCheckpointSaver
from geosearch.clock import Clock, utc_now
from geosearch.config import GeoConfig
from geosearch.geo.area_store import AreaStore, InMemoryAreaStore, MongoAreaStore
from geosearch.registry.mongo import connect, database, ping


class PersistenceUnavailable(RuntimeError):
    """`conversation.store=mongodb` and MongoDB is unreachable at startup."""


def _no_keep_alive(conversation_id: str, area_id: str) -> None:
    return None


@dataclass
class Persistence:
    checkpointer: BaseCheckpointSaver
    area_store: AreaStore
    conversations: ConversationStore
    keep_alive: Callable[[str, str], None] = _no_keep_alive
    close: Callable[[], None] = field(default=lambda: None)


def open_persistence(cfg: GeoConfig, *, clock: Clock = utc_now) -> Persistence:
    conv = cfg.conversation
    ttl = timedelta(minutes=conv.idle_ttl_minutes)
    if conv.store == "memory":
        return Persistence(
            checkpointer=InMemorySaver(),
            area_store=InMemoryAreaStore(max_entries=cfg.area_store.max_entries),
            conversations=InMemoryConversationStore(ttl, clock),
        )

    client = connect(cfg.mongo)
    if not ping(client):
        client.close()
        raise PersistenceUnavailable(
            # The URI is not echoed: it may carry credentials.
            "MongoDB is unreachable at the configured mongo.uri "
            "(conversation.store=mongodb). Start it with: docker compose up -d"
        )
    db = database(client, cfg.mongo)
    saver = MongoCheckpointSaver.from_config(db, conv, clock=clock)
    areas = MongoAreaStore(
        db[conv.areas_collection], max_entries=cfg.area_store.max_entries, ttl=ttl, clock=clock
    )
    areas.ensure_indexes()
    conversations = MongoConversationStore(db[conv.conversations_collection], ttl, clock)
    conversations.ensure_indexes()

    def keep_alive(conversation_id: str, area_id: str) -> None:
        saver.touch(conversation_id)
        areas.touch(area_id)

    return Persistence(saver, areas, conversations, keep_alive, client.close)
