import type { AppConfig } from '../config';
import { formatLatLon, parseCoordinates } from '../geo/coords';
import type { LatLon } from '../geo/types';

/** One place-search hit. `bbox` (south, west, north, east) frames it when known. */
export interface GeocodeResult {
  id: string;
  label: string;
  detail?: string;
  position: LatLon;
  bbox?: [number, number, number, number];
}

export interface Geocoder {
  search(query: string, opts: { signal?: AbortSignal; near?: LatLon }): Promise<GeocodeResult[]>;
}

interface NominatimHit {
  place_id: number;
  display_name: string;
  name?: string;
  lat: string;
  lon: string;
  boundingbox?: [string, string, string, string];
}

/** OpenStreetMap Nominatim: no key, ~1 request/second (the UI debounces). */
export function createNominatimGeocoder(baseUrl: string, fetchImpl: typeof fetch = fetch): Geocoder {
  return {
    async search(query, { signal, near }) {
      const params = new URLSearchParams({
        q: query,
        format: 'jsonv2',
        limit: '6',
        'accept-language': 'he,en',
      });
      if (near) {
        // Prefer (but don't restrict to) places around the current view.
        const d = 0.5;
        params.set('viewbox', `${near.lon - d},${near.lat + d},${near.lon + d},${near.lat - d}`);
      }
      const res = await fetchImpl(`${baseUrl}/search?${params}`, { signal });
      if (!res.ok) throw new Error(`geocoder HTTP ${res.status}`);
      const hits = (await res.json()) as NominatimHit[];
      return hits.map((h) => {
        const [label, ...rest] = h.display_name.split(',').map((s) => s.trim());
        const bb = h.boundingbox?.map(Number);
        return {
          id: String(h.place_id),
          label: h.name || label || h.display_name,
          detail: rest.slice(0, 3).join(', ') || undefined,
          position: { lat: Number(h.lat), lon: Number(h.lon) },
          bbox: bb && bb.every(Number.isFinite) ? [bb[0]!, bb[2]!, bb[1]!, bb[3]!] : undefined,
        };
      });
    },
  };
}

/** Typed coordinates resolve locally, before (or instead of) any place search. */
export function coordinateHit(query: string): GeocodeResult | null {
  const pos = parseCoordinates(query);
  return pos ? { id: 'coords', label: formatLatLon(pos), position: pos } : null;
}

export function createGeocoder(cfg: AppConfig): Geocoder | null {
  return cfg.geocoder === 'nominatim' ? createNominatimGeocoder(cfg.nominatimUrl) : null;
}
