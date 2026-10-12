import type { Polygon } from 'geojson';

/** How the user drew the area. Every kind is sent to the backend as a Polygon. */
export type AreaKind = 'polygon' | 'rect' | 'circle' | 'free';

/**
 * The user's area: a closed GeoJSON Polygon in lon/lat (WGS84). Circles are
 * turned into a polygon here, in the browser, because the backend's own Point
 * buffer is fixed at a configured radius.
 */
export interface Area {
  geometry: Polygon;
  kind: AreaKind;
  /** The N in "מצולע N": counts the areas drawn in this browser. */
  seq: number;
  km2: number;
}

export interface LatLon {
  lat: number;
  lon: number;
}
