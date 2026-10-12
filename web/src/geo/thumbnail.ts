import type { Polygon } from 'geojson';

/**
 * An SVG path for the area, fitted into a size×size box with padding. Uses a
 * local equirectangular projection (cos(lat) scaling) so shapes keep their look.
 */
export function thumbnailPath(geometry: Polygon, size: number, pad = 6): string {
  const outer = geometry.coordinates[0] ?? [];
  if (outer.length === 0) return '';
  const midLat = outer.reduce((s, p) => s + p[1]!, 0) / outer.length;
  const k = Math.cos((midLat * Math.PI) / 180);
  const pts = outer.map((p) => [p[0]! * k, -p[1]!] as const);
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const w = Math.max(...xs) - minX || 1e-9;
  const h = Math.max(...ys) - minY || 1e-9;
  const scale = (size - pad * 2) / Math.max(w, h);
  const offX = (size - w * scale) / 2;
  const offY = (size - h * scale) / 2;
  return (
    pts
      .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${((x - minX) * scale + offX).toFixed(1)} ${((y - minY) * scale + offY).toFixed(1)}`)
      .join(' ') + ' Z'
  );
}
