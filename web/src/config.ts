/**
 * Every deployment-specific value lives here, read once from Vite env vars
 * (see .env.example). Components import `config`, never `import.meta.env`, so
 * swapping a basemap, geocoder or API target is a config change, not a code change.
 */

export interface BasemapConfig {
  id: string;
  /** Hebrew name, announced by the base-map button. */
  label: string;
  url: string;
  attribution: string;
  maxZoom?: number;
  subdomains?: string;
  /** Tiles for the dark theme. Without it, dark mode inverts these tiles with
   *  a CSS filter, unless `darkFilter` is false (imagery should stay as is). */
  darkUrl?: string;
  darkFilter?: boolean;
}

export type GeocoderKind = 'nominatim' | 'none';
export type ApiMode = 'http' | 'mock';

export interface AppConfig {
  apiMode: ApiMode;
  apiBaseUrl: string;
  requestTimeoutMs: number;
  maxAreaKm2: number;
  maxPromptChars: number;
  basemaps: BasemapConfig[];
  geocoder: GeocoderKind;
  nominatimUrl: string;
  initialView: { lat: number; lon: number; zoom: number };
}

export const DEFAULT_BASEMAPS: BasemapConfig[] = [
  {
    id: 'osm',
    label: 'מפת רחובות',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  },
  {
    id: 'esri-imagery',
    label: 'תצלום אוויר',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
    darkFilter: false,
  },
];

type Env = Record<string, string | undefined>;

function num(value: string | undefined, fallback: number): number {
  if (value === undefined || value.trim() === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseBasemaps(raw: string | undefined): BasemapConfig[] {
  if (!raw || raw.trim() === '') return DEFAULT_BASEMAPS;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('not a list');
    const valid = parsed.filter(
      (b): b is BasemapConfig =>
        typeof b === 'object' &&
        b !== null &&
        typeof (b as BasemapConfig).id === 'string' &&
        typeof (b as BasemapConfig).url === 'string' &&
        typeof (b as BasemapConfig).label === 'string',
    );
    if (valid.length === 0) throw new Error('no valid basemap');
    return valid.map((b) => ({ ...b, attribution: b.attribution ?? '' }));
  } catch (err) {
    console.warn('VITE_BASEMAPS is invalid, using the defaults:', err);
    return DEFAULT_BASEMAPS;
  }
}

function parseView(raw: string | undefined): AppConfig['initialView'] {
  const fallback = { lat: 32.0853, lon: 34.7818, zoom: 13 };
  if (!raw) return fallback;
  const [lat, lon, zoom] = raw.split(',').map((part) => Number(part.trim()));
  if ([lat, lon, zoom].some((v) => v === undefined || !Number.isFinite(v))) return fallback;
  return { lat: lat!, lon: lon!, zoom: zoom! };
}

export function readConfig(env: Env): AppConfig {
  return {
    apiMode: env.VITE_API_MODE === 'mock' ? 'mock' : 'http',
    apiBaseUrl: (env.VITE_API_BASE_URL ?? '').replace(/\/+$/, ''),
    requestTimeoutMs: num(env.VITE_REQUEST_TIMEOUT_S, 600) * 1000,
    maxAreaKm2: num(env.VITE_MAX_AREA_KM2, 100),
    maxPromptChars: num(env.VITE_MAX_PROMPT_CHARS, 2000),
    basemaps: parseBasemaps(env.VITE_BASEMAPS),
    geocoder: env.VITE_GEOCODER === 'none' ? 'none' : 'nominatim',
    nominatimUrl: (env.VITE_NOMINATIM_URL || 'https://nominatim.openstreetmap.org').replace(
      /\/+$/,
      '',
    ),
    initialView: parseView(env.VITE_MAP_INITIAL_VIEW),
  };
}

export const config: AppConfig = readConfig(import.meta.env as Env);
