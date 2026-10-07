"""Stage 6 persistence wiring that needs no MongoDB: config and the memory backend."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from geosearch.agent.persistence import open_persistence
from geosearch.config import ConversationConfig, GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore


def test_conversation_defaults(cfg: GeoConfig) -> None:
    conv = cfg.conversation
    assert conv.store == "memory"
    assert conv.idle_ttl_minutes == 7 * 24 * 60
    assert conv.collection_names == ("checkpoints", "checkpoint_writes", "conversations", "areas")


@pytest.mark.parametrize(
    "override",
    [
        {"conversations_collection": ""},
        {"areas_collection": "checkpoints"},  # duplicate name
        {"areas_collection": "tools"},  # the registry's collection
        {"checkpoint_warn_bytes": 0},
        {"idle_ttl_minutes": 0},
    ],
)
def test_conversation_config_rejects(override: dict) -> None:
    with pytest.raises(ValidationError):
        ConversationConfig(**override)


def test_memory_backend(cfg: GeoConfig) -> None:
    persistence = open_persistence(cfg)
    assert isinstance(persistence.checkpointer, InMemorySaver)
    assert isinstance(persistence.area_store, InMemoryAreaStore)
    persistence.close()  # a no-op, but always safe to call
