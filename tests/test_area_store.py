from concurrent.futures import ThreadPoolExecutor

import pytest
import shapely

from geosearch.geo.area_store import AreaNotFound, InMemoryAreaStore, make_area_id


def _square(a: float, b: float, c: float, d: float) -> shapely.Geometry:
    return shapely.from_wkt(f"POLYGON(({a} {b}, {c} {b}, {c} {d}, {a} {d}, {a} {b}))")


def test_same_wkt_gives_same_id() -> None:
    a = _square(0, 0, 4, 4)
    b = _square(0, 0, 4, 4)
    assert make_area_id(a) == make_area_id(b)


def test_vertex_order_variants_give_same_id() -> None:
    square = shapely.from_wkt("POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))")
    rotated_start = shapely.from_wkt("POLYGON((4 4, 0 4, 0 0, 4 0, 4 4))")
    reversed_ring = shapely.from_wkt("POLYGON((0 0, 0 4, 4 4, 4 0, 0 0))")
    assert make_area_id(square) == make_area_id(rotated_start) == make_area_id(reversed_ring)


def test_different_areas_give_different_ids() -> None:
    assert make_area_id(_square(0, 0, 4, 4)) != make_area_id(_square(0, 0, 5, 5))


def test_area_id_has_expected_shape() -> None:
    area_id = make_area_id(_square(0, 0, 1, 1))
    assert area_id.startswith("area_")
    assert len(area_id) == len("area_") + 12


# --- InMemoryAreaStore ---


def test_put_then_get_round_trip() -> None:
    store = InMemoryAreaStore(max_entries=10)
    original = _square(0, 0, 4, 4)
    area_id = store.put(original)
    retrieved = store.get(area_id)
    assert retrieved.equals(original)


def test_put_same_area_twice_returns_same_id() -> None:
    store = InMemoryAreaStore(max_entries=10)
    id_a = store.put(_square(0, 0, 4, 4))
    id_b = store.put(_square(0, 0, 4, 4))
    assert id_a == id_b


def test_get_unknown_id_raises_area_not_found() -> None:
    store = InMemoryAreaStore(max_entries=10)
    with pytest.raises(AreaNotFound):
        store.get("area_doesnotexist")


def test_eviction_is_least_recently_used() -> None:
    store = InMemoryAreaStore(max_entries=2)
    id_a = store.put(_square(0, 0, 1, 1))
    id_b = store.put(_square(10, 10, 11, 11))

    store.get(id_a)  # touch A so it's more recently used than B

    id_c = store.put(_square(20, 20, 21, 21))  # should evict B, not A

    assert store.get(id_a) is not None
    assert store.get(id_c) is not None
    with pytest.raises(AreaNotFound):
        store.get(id_b)


def test_put_does_not_evict_when_reinserting_existing_area() -> None:
    store = InMemoryAreaStore(max_entries=2)
    id_a = store.put(_square(0, 0, 1, 1))
    id_b = store.put(_square(10, 10, 11, 11))

    store.put(_square(0, 0, 1, 1))  # re-insert A; must not evict B

    assert store.get(id_a) is not None
    assert store.get(id_b) is not None


def test_concurrent_put_and_get_is_thread_safe() -> None:
    store = InMemoryAreaStore(max_entries=20)
    squares = [_square(i, i, i + 1, i + 1) for i in range(50)]

    def worker(square: shapely.Geometry) -> None:
        area_id = store.put(square)
        try:
            store.get(area_id)
        except AreaNotFound:
            pass  # may have been evicted by a concurrent put; not a bug

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(worker, squares * 4))

    assert len(store._areas) <= 20
