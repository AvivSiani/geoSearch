import type { MultiPolygon, Polygon, Position } from 'geojson';

/** Full precision, no exponent for tiny values, and never "-0". */
function coord(value: number): string {
  if (Object.is(value, -0) || value === 0) return '0';
  const s = String(value);
  return s.includes('e') ? value.toFixed(12).replace(/\.?0+$/, '') : s;
}

function ring(positions: Position[]): string {
  if (positions.length === 0) throw new Error('empty ring');
  const first = positions[0]!;
  const last = positions[positions.length - 1]!;
  const closed = first[0] === last[0] && first[1] === last[1] ? positions : [...positions, first];
  return `(${closed.map((p) => `${coord(p[0]!)} ${coord(p[1]!)}`).join(', ')})`;
}

function polygonBody(rings: Position[][]): string {
  return `(${rings.map(ring).join(', ')})`;
}

/**
 * GeoJSON → WKT, lon-lat order like the backend expects. The geometry is sent
 * exactly as drawn: the backend never repairs it, so neither do we.
 */
export function toWkt(geometry: Polygon | MultiPolygon): string {
  if (geometry.type === 'Polygon') return `POLYGON ${polygonBody(geometry.coordinates)}`;
  return `MULTIPOLYGON (${geometry.coordinates.map(polygonBody).join(', ')})`;
}
