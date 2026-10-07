"""Area storage: after validation, areas are referenced only by area_id
(invariant 3). area_id is server-generated only (invariant 4).

InMemoryAreaStore is the Stage 1 LRU; MongoAreaStore (Stage 6) persists areas
so a conversation's area survives a restart, keeping the LRU as its cache.
"""

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

import shapely
from pymongo.collection import Collection
from shapely.geometry import MultiPolygon, Polygon

Area = Polygon | MultiPolygon


def make_area_id(area: Area) -> str:
    """Deterministic id: the same area always gets the same id.

    Normalizing before hashing means vertex order and ring start point don't
    change the id (see test: a square described starting from any corner, or
    wound in either direction, normalizes to the same canonical WKT). Rounding
    to 7 decimal places (~1cm) treats float noise as the same area. Hashing
    the *final* geometry means a changed buffer radius yields a different id.
    """
    normalized = shapely.normalize(area)
    key = shapely.to_wkt(normalized, rounding_precision=7, trim=True)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"area_{digest[:12]}"


def area_to_wkt(area: Area) -> str:
    """Full-precision WKT, so reading it back yields the same shape and the same
    area_id. shapely's default rounds to 6 decimals while make_area_id hashes at
    7, so a default-rounded round-trip could silently change the area
    (invariant 1)."""
    return shapely.to_wkt(area, rounding_precision=-1)


class AreaNotFound(Exception):
    """Internal error, not a client error: clients never send an area_id, so a
    missing area_id signals a bug in our own code, not bad input."""


class AreaStore(Protocol):
    """Lets Stage 1's in-memory store be swapped for e.g. MongoDB later
    without touching anything that depends on this protocol."""

    def put(self, area: Area) -> str: ...
    def get(self, area_id: str) -> Area: ...


class InMemoryAreaStore:
    """Bounded LRU cache of areas, keyed by their deterministic area_id.

    Guarded by a lock because FastAPI runs sync endpoints in a thread pool, so
    concurrent requests can call put()/get() on the same store instance.
    """

    def __init__(self, max_entries: int):
        self._max_entries = max_entries
        self._areas: OrderedDict[str, Area] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, area: Area) -> str:
        area_id = make_area_id(area)
        with self._lock:
            if area_id in self._areas:
                self._areas.move_to_end(area_id)
                return area_id
            normalized = shapely.normalize(area)
            shapely.prepare(normalized)
            self._areas[area_id] = normalized
            if len(self._areas) > self._max_entries:
                self._areas.popitem(last=False)
        return area_id

    def __contains__(self, area_id: str) -> bool:
        with self._lock:
            return area_id in self._areas

    def get(self, area_id: str) -> Area:
        with self._lock:
            try:
                area = self._areas[area_id]
            except KeyError as exc:
                raise AreaNotFound(area_id) from exc
            self._areas.move_to_end(area_id)
            return area


class MongoAreaStore:
    """Areas that outlive the process (Stage 6): write-through to MongoDB on put,
    read-through on get, with the in-memory LRU as the cache in front.

    Stored as WKB of the normalized shape, which is exact; WKT could round (see
    area_to_wkt). area_id is a content hash, so a put is an idempotent upsert
    and areas are shared by every conversation on the same shape. `expires_at`
    only ever moves forward (`$max`): `touch` pushes it out once per turn, so an
    area lives as long as the longest-lived conversation using it.
    """

    def __init__(
        self,
        collection: Collection,
        *,
        max_entries: int,
        ttl: timedelta,
        clock: Callable[[], datetime],
    ):
        self._collection = collection
        self._cache = InMemoryAreaStore(max_entries=max_entries)
        self._ttl = ttl
        self._clock = clock

    def ensure_indexes(self) -> None:
        self._collection.create_index("expires_at", expireAfterSeconds=0)

    def put(self, area: Area) -> str:
        area_id = make_area_id(area)
        if area_id not in self._cache:
            self._upsert(area_id, shapely.normalize(area))
        return self._cache.put(area)

    def get(self, area_id: str) -> Area:
        if area_id in self._cache:
            return self._cache.get(area_id)
        doc = self._collection.find_one({"_id": area_id, "expires_at": {"$gt": self._clock()}})
        if doc is None:  # unknown, or expired and not yet swept by the TTL monitor
            raise AreaNotFound(area_id)
        cached_id = self._cache.put(shapely.from_wkb(doc["wkb"]))
        if cached_id != area_id:  # WKB is exact, so this would be a bug
            raise AreaNotFound(f"{area_id} reloaded as {cached_id}")
        return self._cache.get(area_id)

    def touch(self, area_id: str) -> None:
        """Keep the area alive with its conversation. If the document is gone
        while the process still caches the shape, write it back."""
        result = self._collection.update_one(
            {"_id": area_id}, {"$max": {"expires_at": self._expires_at()}}
        )
        if result.matched_count == 0 and area_id in self._cache:
            self._upsert(area_id, self._cache.get(area_id))

    def _upsert(self, area_id: str, normalized: Area) -> None:
        self._collection.update_one(
            {"_id": area_id},
            {
                "$setOnInsert": {
                    "wkb": shapely.to_wkb(normalized),
                    "geometry_type": normalized.geom_type,
                    "created_at": self._clock(),
                },
                "$max": {"expires_at": self._expires_at()},
            },
            upsert=True,
        )

    def _expires_at(self) -> datetime:
        return self._clock() + self._ttl
