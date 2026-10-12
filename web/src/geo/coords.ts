import type { LatLon } from './types';

/** "32.0853° N, 34.7818° E": always rendered dir="ltr" in the mono font. */
export function formatLatLon({ lat, lon }: LatLon, digits = 4): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(lon).toFixed(digits)}° ${ew}`;
}

/** Wraps any longitude into [-180, 180). */
export function wrapLon(lon: number): number {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

/**
 * Reads coordinates typed into the map search: "32.08, 34.78",
 * "32.08 34.78", "32.08° N, 34.78° E" or "34.78° E 32.08° N".
 * Unlabelled pairs are lat, lon (the usual order in Israel's map apps).
 */
export function parseCoordinates(text: string): LatLon | null {
  const re = /(-?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?/gi;
  const parts: { value: number; hemi?: string }[] = [];
  for (const m of text.trim().matchAll(re)) {
    parts.push({ value: Number(m[1]), hemi: m[2]?.toUpperCase() });
  }
  if (parts.length !== 2) return null;
  const leftover = text.replace(re, '').replace(/[\s,;]/g, '');
  if (leftover !== '') return null;

  let lat: number | undefined;
  let lon: number | undefined;
  for (const [i, p] of parts.entries()) {
    const signed = p.hemi === 'S' || p.hemi === 'W' ? -Math.abs(p.value) : p.value;
    if (p.hemi === 'N' || p.hemi === 'S') lat = signed;
    else if (p.hemi === 'E' || p.hemi === 'W') lon = signed;
    else if (i === 0 && lat === undefined) lat = signed;
    else lon ??= signed;
  }
  if (lat === undefined || lon === undefined) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}
