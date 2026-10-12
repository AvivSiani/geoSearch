import type { FeatureCollection, Point } from 'geojson';
import type { ResponseItem } from '../api/types';

/** Results as GeoJSON points; items without a location are left out. */
export function toGeoJson(items: ResponseItem[]): FeatureCollection<Point> {
  return {
    type: 'FeatureCollection',
    features: items.flatMap((item, i) =>
      item.lat === null || item.lon === null
        ? []
        : [
            {
              type: 'Feature' as const,
              geometry: { type: 'Point' as const, coordinates: [item.lon, item.lat] },
              properties: { n: i + 1, id: item.id, source_id: item.source_id, name: item.name, ...item.data },
            },
          ],
    ),
  };
}

export function downloadGeoJson(items: ResponseItem[], filename: string): void {
  const blob = new Blob([JSON.stringify(toGeoJson(items), null, 2)], { type: 'application/geo+json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
