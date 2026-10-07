"""Conversation bookkeeping: the checkpointer that persists thread state, and an
in-memory registry that enforces the conversation-level rules (one turn at a
time, a turn cap, an idle TTL).

The registry holds only lightweight metadata (ids, timestamps, a lock). The
actual conversation state — messages, files, our shared fields — lives in the
checkpointer, keyed by the same conversation_id used as the thread_id.
"""

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver

from geosearch.config import ConversationConfig, GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError


def make_checkpointer(cfg: GeoConfig) -> BaseCheckpointSaver:
    """The checkpointer alone, for tests and tools that build an agent without
    the app. The app uses agent/persistence.py, which picks every backend."""
    from geosearch.agent.persistence import open_persistence

    return open_persistence(cfg).checkpointer


@dataclass
class ConversationEntry:
    """One conversation's metadata. The lock serializes turns; `turn` is the
    number of turns already completed."""

    area_id: str
    created_at: float
    last_used: float
    turn: int
    lock: threading.Lock


class ConversationRegistry:
    """In-memory registry of live conversations.

    `clock` is injectable so tests can fast-forward past the idle TTL without
    sleeping. It must return monotonically increasing seconds.
    """

    def __init__(
        self,
        cfg: ConversationConfig,
        checkpointer: BaseCheckpointSaver,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._cfg = cfg
        self._checkpointer = checkpointer
        self._clock = clock
        self._entries: dict[str, ConversationEntry] = {}
        self._guard = threading.Lock()  # protects the _entries dict itself

    def create(self, area_id: str) -> str:
        """Register a fresh conversation bound to `area_id` and return its id."""
        conversation_id = uuid.uuid4().hex
        now = self._clock()
        entry = ConversationEntry(
            area_id=area_id, created_at=now, last_used=now, turn=0, lock=threading.Lock()
        )
        with self._guard:
            self._entries[conversation_id] = entry
        return conversation_id

    def _live_entry(self, conversation_id: str) -> ConversationEntry:
        """Return the entry, treating an unknown or idle-expired conversation as
        not found (and cleaning up an expired one's thread)."""
        ttl_seconds = self._cfg.idle_ttl_minutes * 60
        with self._guard:
            entry = self._entries.get(conversation_id)
            if entry is None:
                raise GeoValidationError(
                    ErrorCode.CONVERSATION_NOT_FOUND,
                    "unknown conversation_id",
                    {"conversation_id": conversation_id},
                )
            if self._clock() - entry.last_used > ttl_seconds:
                del self._entries[conversation_id]
                self._delete_thread(conversation_id)
                raise GeoValidationError(
                    ErrorCode.CONVERSATION_NOT_FOUND,
                    "conversation expired",
                    {"conversation_id": conversation_id},
                )
            return entry

    def _delete_thread(self, conversation_id: str) -> None:
        deleter = getattr(self._checkpointer, "delete_thread", None)
        if callable(deleter):
            deleter(conversation_id)

    @contextmanager
    def turn(self, conversation_id: str) -> Iterator[tuple[ConversationEntry, int]]:
        """Hold the conversation for one turn.

        Yields (entry, next_turn). The lock is acquired without blocking, so a
        concurrent turn fails fast as CONVERSATION_BUSY rather than queueing.
        The turn count and last-used time advance only if the body succeeds — a
        failed turn neither counts against the cap nor keeps the conversation alive.
        """
        entry = self._live_entry(conversation_id)
        if not entry.lock.acquire(blocking=False):
            raise GeoValidationError(
                ErrorCode.CONVERSATION_BUSY,
                "conversation is already running a turn",
                {"conversation_id": conversation_id},
            )
        try:
            next_turn = entry.turn + 1
            if next_turn > self._cfg.max_turns:
                raise GeoValidationError(
                    ErrorCode.CONVERSATION_LIMIT,
                    f"conversation reached its {self._cfg.max_turns}-turn limit",
                    {"conversation_id": conversation_id, "max_turns": self._cfg.max_turns},
                )
            yield entry, next_turn
            entry.turn = next_turn
            entry.last_used = self._clock()
        finally:
            entry.lock.release()
