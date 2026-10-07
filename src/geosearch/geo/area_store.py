"""Area storage: after validation, areas are referenced only by area_id
(invariant 3). area_id is server-generated only (invariant 4).
"""

import hashlib
import threading
from collections import OrderedDict
from typing import Protocol

import shapely
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

    def get(self, area_id: str) -> Area:
        with self._lock:
            try:
                area = self._areas[area_id]
            except KeyError as exc:
                raise AreaNotFound(area_id) from exc
            self._areas.move_to_end(area_id)
            return area
