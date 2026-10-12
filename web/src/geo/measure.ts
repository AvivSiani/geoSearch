import turfArea from '@turf/area';
import kinks from '@turf/kinks';
import type { Polygon } from 'geojson';

export function areaKm2(geometry: Polygon): number {
  return turfArea(geometry) / 1_000_000;
}

export type AreaIssue =
  | { kind: 'too_few_points' }
  | { kind: 'self_intersects' }
  | { kind: 'too_large'; km2: number; maxKm2: number };

/**
 * Early, client-side feedback mirroring the backend checks that a drawing can
 * fail. The backend stays the authority; this only saves a round-trip.
 */
export function findAreaIssue(geometry: Polygon, maxKm2: number): AreaIssue | null {
  const outer = geometry.coordinates[0] ?? [];
  const distinct = new Set(outer.map((p) => `${p[0]},${p[1]}`));
  if (distinct.size < 3) return { kind: 'too_few_points' };
  if (kinks(geometry).features.length > 0) return { kind: 'self_intersects' };
  const km2 = areaKm2(geometry);
  if (km2 > maxKm2) return { kind: 'too_large', km2, maxKm2 };
  return null;
}
