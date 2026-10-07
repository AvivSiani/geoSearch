"""A LangGraph checkpointer on MongoDB (Stage 6, D1).

Why our own rather than `langgraph-checkpoint-mongodb`: that package needs
pymongo<4.18 (we pin 4.18.2) and pulls a vector-search stack we don't use, and
its per-document `created_at` TTL would expire a live conversation's oldest
checkpoints first.

Why full history rather than only the latest checkpoint: deepagents declares
`messages` and `files` as DeltaChannels. A checkpoint stores only a marker for
them; their values are rebuilt by walking the parent chain and its pending
writes back to the last snapshot. Dropping ancestors would silently rebuild them
empty. So every checkpoint is kept, and `touch` refreshes the whole thread's
`expires_at` each turn: the chain expires all at once, never piecemeal. Growth
stays small because turns run with `durability="exit"` (1-2 checkpoints a turn).

Semantics mirror InMemorySaver (langgraph-checkpoint 4.2.0): the latest
checkpoint is the highest checkpoint_id; a regular write is insert-only, a
special one (negative WRITES_IDX_MAP index: error, interrupt, ...) overwrites;
pending writes come back in insertion order, which delta replay depends on.
"""

import logging
import random
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from pymongo import ASCENDING, DESCENDING, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database

from geosearch.clock import Clock, utc_now
from geosearch.config import ConversationConfig

log = logging.getLogger(__name__)


class MongoCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(
        self,
        checkpoints: Collection,
        writes: Collection,
        *,
        ttl: timedelta,
        warn_bytes: int,
        clock: Clock = utc_now,
    ):
        super().__init__()
        self._checkpoints = checkpoints
        self._writes = writes
        self._ttl = ttl
        self._warn_bytes = warn_bytes
        self._clock = clock

    @classmethod
    def from_config(
        cls, db: Database, cfg: ConversationConfig, *, clock: Clock = utc_now
    ) -> "MongoCheckpointSaver":
        """Collections, retention and size warning all come from config."""
        saver = cls(
            db[cfg.checkpoints_collection],
            db[cfg.checkpoint_writes_collection],
            ttl=timedelta(minutes=cfg.idle_ttl_minutes),
            warn_bytes=cfg.checkpoint_warn_bytes,
            clock=clock,
        )
        saver.ensure_indexes()
        return saver

    def ensure_indexes(self) -> None:
        """Idempotent. TTL indexes use expireAfterSeconds=0 on `expires_at`, so a
        retention change never needs an index rebuild."""
        self._checkpoints.create_index(
            [("thread_id", ASCENDING), ("checkpoint_ns", ASCENDING), ("checkpoint_id", DESCENDING)],
            unique=True,
        )
        self._writes.create_index(
            [
                ("thread_id", ASCENDING),
                ("checkpoint_ns", ASCENDING),
                ("checkpoint_id", ASCENDING),
                ("task_id", ASCENDING),
                ("idx", ASCENDING),
            ],
            unique=True,
        )
        for collection in (self._checkpoints, self._writes):
            collection.create_index("expires_at", expireAfterSeconds=0)

    # -- reads -------------------------------------------------------------

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        query: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
        if checkpoint_id := get_checkpoint_id(config):
            query["checkpoint_id"] = checkpoint_id
        doc = self._checkpoints.find_one(query, sort=[("checkpoint_id", DESCENDING)])
        return self._to_tuple(doc) if doc else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """Newest first. `filter` matches metadata keys in Python (metadata is
        stored serialized); nothing in the app lists checkpoints, so this
        favors exactness over query speed."""
        query: dict[str, Any] = {}
        if config is not None:
            configurable = config["configurable"]
            query["thread_id"] = configurable["thread_id"]
            if "checkpoint_ns" in configurable:
                query["checkpoint_ns"] = configurable["checkpoint_ns"]
            if checkpoint_id := get_checkpoint_id(config):
                query["checkpoint_id"] = checkpoint_id
        if before is not None and (before_id := get_checkpoint_id(before)):
            query.setdefault("checkpoint_id", {})
            if isinstance(query["checkpoint_id"], dict):
                query["checkpoint_id"]["$lt"] = before_id
            elif query["checkpoint_id"] >= before_id:
                return
        remaining = limit
        for doc in self._checkpoints.find(query, sort=[("checkpoint_id", DESCENDING)]):
            if remaining is not None and remaining <= 0:
                return
            tup = self._to_tuple(doc)
            if filter and not all(tup.metadata.get(k) == v for k, v in filter.items()):
                continue
            if remaining is not None:
                remaining -= 1
            yield tup

    def _to_tuple(self, doc: dict[str, Any]) -> CheckpointTuple:
        thread_id, checkpoint_ns = doc["thread_id"], doc["checkpoint_ns"]
        checkpoint_id = doc["checkpoint_id"]
        writes = self._writes.find(
            {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            },
            sort=[("seq", ASCENDING), ("pos", ASCENDING)],
        )
        parent_id = doc.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=self.serde.loads_typed((doc["type"], doc["checkpoint"])),
            metadata=self.serde.loads_typed((doc["metadata_type"], doc["metadata"])),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=[
                (w["task_id"], w["channel"], self.serde.loads_typed((w["type"], w["value"])))
                for w in writes
            ],
        )

    # -- writes ------------------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        type_, payload = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_payload = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        self._warn_if_large("checkpoint", thread_id, len(payload) + len(metadata_payload))
        self._checkpoints.update_one(
            {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            },
            {
                "$set": {
                    "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
                    "type": type_,
                    "checkpoint": payload,
                    "metadata_type": metadata_type,
                    "metadata": metadata_payload,
                    "expires_at": self._expires_at(),
                }
            },
            upsert=True,
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        if not writes:
            return
        configurable = config["configurable"]
        key = {
            "thread_id": configurable["thread_id"],
            "checkpoint_ns": configurable.get("checkpoint_ns", ""),
            "checkpoint_id": configurable["checkpoint_id"],
            "task_id": task_id,
        }
        seq = time.time_ns()  # insertion order across calls; pos orders within one
        expires_at = self._expires_at()
        ops = []
        for pos, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, pos)
            type_, payload = self.serde.dumps_typed(value)
            self._warn_if_large("write", key["thread_id"], len(payload))
            fields = {
                "channel": channel,
                "type": type_,
                "value": payload,
                "task_path": task_path,
                "seq": seq,
                "pos": pos,
                "expires_at": expires_at,
            }
            # Like InMemorySaver: a regular write is kept once, a special one replaced.
            update = {"$set": fields} if idx < 0 else {"$setOnInsert": fields}
            ops.append(UpdateOne({**key, "idx": idx}, update, upsert=True))
        self._writes.bulk_write(ops, ordered=True)

    def delete_thread(self, thread_id: str) -> None:
        self._checkpoints.delete_many({"thread_id": thread_id})
        self._writes.delete_many({"thread_id": thread_id})

    def touch(self, thread_id: str) -> None:
        """Push the whole thread's expiry out by the TTL. Called once per
        finished turn, so a live conversation never loses an ancestor."""
        update = {"$set": {"expires_at": self._expires_at()}}
        self._checkpoints.update_many({"thread_id": thread_id}, update)
        self._writes.update_many({"thread_id": thread_id}, update)

    # The app drives the graph synchronously; these exist so an async caller
    # gets the same behavior rather than the base class's NotImplementedError.
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for tup in self.list(config, filter=filter, before=before, limit=limit):
            yield tup

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        """InMemorySaver's scheme: a zero-padded counter plus a random tiebreak."""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    def _expires_at(self) -> datetime:
        return self._clock() + self._ttl

    def _warn_if_large(self, kind: str, thread_id: str, size: int) -> None:
        """Warn-only: truncating would change the conversation. MongoDB rejects
        a document above 16 MB, so this fires well before that."""
        if size > self._warn_bytes:
            log.warning(
                "checkpoint store: %s of %d bytes for thread %s (warn above %d)",
                kind,
                size,
                thread_id,
                self._warn_bytes,
            )
