import type { Polygon, Position } from 'geojson';
import type { LatLon } from './types';

const EARTH_RADIUS_M = 6_371_008.8;
const toRad = (d: number) => (d * Math.PI) / 180;
const toDeg = (r: number) => (r * 180) / Math.PI;

/** The point `distanceM` from `center` along `bearingDeg` on a sphere. */
export function destination(center: LatLon, distanceM: number, bearingDeg: number): Position {
  const lat1 = toRad(center.lat);
  const lon1 = toRad(center.lon);
  const brng = toRad(bearingDeg);
  const d = distanceM / EARTH_RADIUS_M;
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(brng));
  const lon2 =
    lon1 + Math.atan2(Math.sin(brng) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
  return [toDeg(lon2), toDeg(lat2)];
}

/** A drawn circle as a closed polygon ring (clockwise from north). */
export function circleToPolygon(center: LatLon, radiusM: number, segments = 64): Polygon {
  const ring: Position[] = [];
  for (let i = 0; i < segments; i++) ring.push(destination(center, radiusM, (360 * i) / segments));
  ring.push(ring[0]!);
  return { type: 'Polygon', coordinates: [ring] };
}
