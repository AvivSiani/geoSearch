"""Deterministic geo operations, addressed only by area_id (invariant 3)."""

from pyproj import Geod
from shapely.geometry import Point

from geosearch.geo.area_store import AreaStore
from geosearch.geo.summary import format_area_summary

_GEOD = Geod(ellps="WGS84")


class AreaOps:
    """Geo operations over areas already stored by area_id."""

    def __init__(self, store: AreaStore):
        self._store = store

    def contains(self, area_id: str, lon: float, lat: float) -> bool:
        """Uses `covers`, not `contains`: a point exactly on the boundary
        counts as inside. A point inside a hole is outside — that's not a
        boundary subtlety, the hole simply isn't part of the polygon's area."""
        geom = self._store.get(area_id)
        return geom.covers(Point(lon, lat))

    def representative_point(self, area_id: str) -> tuple[float, float]:
        """Unlike the centroid, representative_point() is guaranteed to lie
        inside the geometry — even for a C-shaped polygon or a MultiPolygon,
        where the centroid can fall outside every part."""
        geom = self._store.get(area_id)
        point = geom.representative_point()
        return (point.x, point.y)

    def distance_m(self, area_id: str, lon: float, lat: float) -> float:
        """Geodesic distance from the area's representative point to (lon, lat)."""
        rep_lon, rep_lat = self.representative_point(area_id)
        _, _, distance = _GEOD.inv(rep_lon, rep_lat, lon, lat)
        return distance

    def area_km2(self, area_id: str) -> float:
        """abs() because the sign reflects ring winding direction, not validity."""
        geom = self._store.get(area_id)
        area, _ = _GEOD.geometry_area_perimeter(geom)
        return abs(area) / 1e6

    def bbox(self, area_id: str) -> tuple[float, float, float, float]:
        return self._store.get(area_id).bounds

    def area_summary(self, area_id: str, *, buffer_radius_m: float | None = None) -> str:
        geom = self._store.get(area_id)
        return format_area_summary(
            area_id=area_id,
            geom_type=geom.geom_type,
            area_km2=self.area_km2(area_id),
            bbox=geom.bounds,
            buffer_radius_m=buffer_radius_m,
        )
