"""Conversation bookkeeping: the conversation record (who, which area, the turn
history) and the registry that enforces the conversation-level rules (one turn
at a time, a turn cap, expiry).

The full agent state — messages, files, our shared fields — lives in the
checkpointer, keyed by the conversation_id used as the thread_id. The record is
the readable history next to it (Stage 6, D4): it is written after a turn's
checkpoint, only for a turn that finished, so a failed turn never counts.

Two record stores behind one protocol: in-memory (unit tests, dev) and MongoDB
(survives a restart). The per-conversation turn lock is always in-process: the
deployment model is one API process (multi-process locking is Stage 7).
"""

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from pymongo.collection import Collection

from geosearch.clock import Clock, as_utc, utc_now
from geosearch.config import ConversationConfig, GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError


def make_checkpointer(cfg: GeoConfig) -> BaseCheckpointSaver:
    """The checkpointer alone, for tests and tools that build an agent without
    the app. The app uses agent/persistence.py, which picks every backend."""
    from geosearch.agent.persistence import open_persistence

    return open_persistence(cfg).checkpointer


@dataclass
class ConversationRecord:
    """One conversation. `user_id` is a placeholder for future per-user
    ownership: always None, never accepted from a client, no logic around it."""

    conversation_id: str
    area_id: str
    area_summary: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    turn_count: int = 0  # turns completed and recorded
    turns: list[dict[str, Any]] = field(default_factory=list)
    user_id: None = None


class ConversationStore(Protocol):
    def create(self, area_id: str, area_summary: str) -> ConversationRecord: ...
    def get(self, conversation_id: str) -> ConversationRecord | None: ...
    def append_turn(self, conversation_id: str, entry: dict[str, Any], expected_count: int) -> bool:
        """Append iff `expected_count` turns are recorded; False otherwise."""
        ...

    def delete(self, conversation_id: str) -> None: ...


class InMemoryConversationStore:
    def __init__(self, ttl: timedelta, clock: Clock = utc_now):
        self._ttl = ttl
        self._clock = clock
        self._records: dict[str, ConversationRecord] = {}
        self._lock = threading.Lock()

    def create(self, area_id: str, area_summary: str) -> ConversationRecord:
        now = self._clock()
        record = ConversationRecord(
            conversation_id=uuid.uuid4().hex,
            area_id=area_id,
            area_summary=area_summary,
            created_at=now,
            updated_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._records[record.conversation_id] = record
        return replace(record, turns=[])

    def get(self, conversation_id: str) -> ConversationRecord | None:
        with self._lock:
            record = self._records.get(conversation_id)
            return replace(record, turns=list(record.turns)) if record else None

    def append_turn(self, conversation_id: str, entry: dict[str, Any], expected_count: int) -> bool:
        now = self._clock()
        with self._lock:
            record = self._records.get(conversation_id)
            if record is None or record.turn_count != expected_count:
                return False
            record.turns.append(entry)
            record.turn_count += 1
            record.updated_at = now
            record.expires_at = now + self._ttl
            return True

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            self._records.pop(conversation_id, None)


class MongoConversationStore:
    """One document per conversation, `_id` = conversation_id, turns embedded
    (max_turns small entries stay far below 16 MB). No user_id index until
    something queries by user (Stage 6, D3)."""

    def __init__(self, collection: Collection, ttl: timedelta, clock: Clock = utc_now):
        self._collection = collection
        self._ttl = ttl
        self._clock = clock

    def ensure_indexes(self) -> None:
        self._collection.create_index("expires_at", expireAfterSeconds=0)

    def create(self, area_id: str, area_summary: str) -> ConversationRecord:
        now = self._clock()
        record = ConversationRecord(
            conversation_id=uuid.uuid4().hex,
            area_id=area_id,
            area_summary=area_summary,
            created_at=now,
            updated_at=now,
            expires_at=now + self._ttl,
        )
        self._collection.insert_one(
            {
                "_id": record.conversation_id,
                "user_id": None,
                "area_id": area_id,
                "area_summary": area_summary,
                "created_at": now,
                "updated_at": now,
                "expires_at": record.expires_at,
                "turn_count": 0,
                "turns": [],
            }
        )
        return record

    def get(self, conversation_id: str) -> ConversationRecord | None:
        doc = self._collection.find_one({"_id": conversation_id})
        if doc is None:
            return None
        return ConversationRecord(
            conversation_id=doc["_id"],
            area_id=doc["area_id"],
            area_summary=doc["area_summary"],
            created_at=as_utc(doc["created_at"]),
            updated_at=as_utc(doc["updated_at"]),
            expires_at=as_utc(doc["expires_at"]),
            turn_count=doc["turn_count"],
            turns=[_turn_from_doc(t) for t in doc["turns"]],
            user_id=doc["user_id"],
        )

    def append_turn(self, conversation_id: str, entry: dict[str, Any], expected_count: int) -> bool:
        now = self._clock()
        result = self._collection.update_one(
            {"_id": conversation_id, "turn_count": expected_count},
            {
                "$push": {"turns": entry},
                "$set": {
                    "turn_count": expected_count + 1,
                    "updated_at": now,
                    "expires_at": now + self._ttl,
                },
            },
        )
        return result.matched_count == 1

    def delete(self, conversation_id: str) -> None:
        self._collection.delete_one({"_id": conversation_id})


def _turn_from_doc(turn: dict[str, Any]) -> dict[str, Any]:
    return {k: as_utc(v) if isinstance(v, datetime) else v for k, v in turn.items()}


class ConversationRegistry:
    """Enforces the conversation rules over a ConversationStore.

    `clock` is injectable so tests can move past the TTL without sleeping.
    """

    def __init__(
        self,
        cfg: ConversationConfig,
        checkpointer: BaseCheckpointSaver,
        *,
        clock: Clock = utc_now,
        store: ConversationStore | None = None,
    ):
        self._cfg = cfg
        self._checkpointer = checkpointer
        self._clock = clock
        self._store = store or InMemoryConversationStore(
            timedelta(minutes=cfg.idle_ttl_minutes), clock
        )
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()  # protects the _locks dict itself

    def create(self, area_id: str, area_summary: str) -> str:
        """Register a fresh conversation bound to `area_id` and return its id."""
        return self._store.create(area_id, area_summary).conversation_id

    def live(self, conversation_id: str) -> ConversationRecord:
        """The record, treating an unknown or expired conversation as not found.
        Expiry is checked here, not left to MongoDB's TTL monitor (which runs
        about once a minute), so it is exact; an expired one is cleaned up."""
        record = self._store.get(conversation_id)
        if record is None:
            raise _not_found(conversation_id, "unknown conversation_id")
        if record.expires_at <= self._clock():
            self.forget(conversation_id)
            raise _not_found(conversation_id, "conversation expired")
        return record

    def forget(self, conversation_id: str) -> None:
        """Drop the record and its checkpoints (expired, or state gone)."""
        self._store.delete(conversation_id)
        deleter = getattr(self._checkpointer, "delete_thread", None)
        if callable(deleter):
            deleter(conversation_id)
        with self._guard:
            self._locks.pop(conversation_id, None)

    def _lock_for(self, conversation_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(conversation_id, threading.Lock())

    @contextmanager
    def turn(self, conversation_id: str) -> Iterator[tuple[ConversationRecord, int]]:
        """Hold the conversation for one turn. Yields (record, next_turn).

        The lock is acquired without blocking, so a concurrent turn fails fast
        as CONVERSATION_BUSY rather than queueing. The body records the turn
        with `record_turn` once it has finished; a failed body records nothing,
        so a failed turn neither counts against the cap nor extends the TTL.
        """
        record = self.live(conversation_id)
        lock = self._lock_for(conversation_id)
        if not lock.acquire(blocking=False):
            raise GeoValidationError(
                ErrorCode.CONVERSATION_BUSY,
                "conversation is already running a turn",
                {"conversation_id": conversation_id},
            )
        try:
            record = self.live(conversation_id)  # re-read under the lock
            next_turn = record.turn_count + 1
            self.check_cap(conversation_id, next_turn)
            yield record, next_turn
        finally:
            lock.release()

    def check_cap(self, conversation_id: str, turn: int) -> None:
        if turn > self._cfg.max_turns:
            raise GeoValidationError(
                ErrorCode.CONVERSATION_LIMIT,
                f"conversation reached its {self._cfg.max_turns}-turn limit",
                {"conversation_id": conversation_id, "max_turns": self._cfg.max_turns},
            )

    def record_turn(self, conversation_id: str, turn: int, entry: dict[str, Any]) -> None:
        """Append a finished turn. The filter on the recorded count catches a
        second writer (another process on the same database)."""
        if not self._store.append_turn(conversation_id, {"turn": turn, **entry}, turn - 1):
            raise GeoValidationError(
                ErrorCode.CONVERSATION_BUSY,
                "another turn was recorded for this conversation meanwhile",
                {"conversation_id": conversation_id, "turn": turn},
            )


def _not_found(conversation_id: str, message: str) -> GeoValidationError:
    return GeoValidationError(
        ErrorCode.CONVERSATION_NOT_FOUND, message, {"conversation_id": conversation_id}
    )
