import L from 'leaflet';
import '@geoman-io/leaflet-geoman-free';
import type { Polygon, Position } from 'geojson';
import type { BasemapConfig } from '../config';
import { circleToPolygon } from '../geo/circle';
import { wrapLon } from '../geo/coords';
import type { AreaKind, LatLon } from '../geo/types';
import { he } from '../i18n/he';

/**
 * Everything Leaflet lives behind this class. React components talk to it
 * through plain methods and callbacks, so replacing the map library (or the
 * drawing plugin) means rewriting this file, not the UI.
 */

export type DrawMode = AreaKind | 'edit';

export interface MapResult {
  id: string;
  n: number;
  lat: number;
  lon: number;
  name: string;
}

export interface GeoMapCallbacks {
  onAreaDrawn(geometry: Polygon, kind: AreaKind): void;
  onAreaEdited(geometry: Polygon): void;
  /** The cursor position, or the map center when the cursor is off the map. */
  onCoords(position: LatLon): void;
  onResultHover(id: string | null): void;
  onResultSelect(id: string): void;
  /** Drawing or editing started or stopped (finished, Escape, replaced). */
  onArmedChange(armed: boolean): void;
  onTileError(): void;
}

export interface GeoMapOptions {
  basemaps: BasemapConfig[];
  initialView: { lat: number; lon: number; zoom: number };
  callbacks: GeoMapCallbacks;
}

// Colors come from the design tokens; Leaflet needs them as values, not CSS vars.
const ACCENT = '#2B84C9';
const AREA_STYLE: L.PathOptions = {
  color: ACCENT,
  weight: 3,
  lineJoin: 'round',
  lineCap: 'round',
  fillColor: ACCENT,
  fillOpacity: 0.26,
};
const DRAW_STYLE = {
  templineStyle: { color: ACCENT, weight: 3 },
  hintlineStyle: { color: ACCENT, weight: 2, dashArray: [5, 5] },
  pathOptions: AREA_STYLE,
};

const GEOMAN_SHAPE: Record<'polygon' | 'rect' | 'circle', 'Polygon' | 'Rectangle' | 'Circle'> = {
  polygon: 'Polygon',
  rect: 'Rectangle',
  circle: 'Circle',
};

const SVG_NS = 'http://www.w3.org/2000/svg';

function toPosition(ll: L.LatLng): Position {
  return [wrapLon(ll.lng), ll.lat];
}

/** Leaflet polygon rings → a closed GeoJSON Polygon (outer ring only; drawn shapes have no holes). */
function layerToPolygon(layer: L.Polygon): Polygon {
  const rings = layer.getLatLngs() as L.LatLng[][] | L.LatLng[];
  const outer = (Array.isArray(rings[0]) ? rings[0] : rings) as L.LatLng[];
  const ring = outer.map(toPosition);
  if (ring.length > 0) ring.push([...ring[0]!]);
  return { type: 'Polygon', coordinates: [ring] };
}

function polygonToLatLngs(geometry: Polygon): L.LatLngExpression[][] {
  return geometry.coordinates.map((ring) =>
    // Leaflet closes rings itself; drop the repeated last vertex.
    ring.slice(0, -1).map((p) => [p[1]!, p[0]!] as L.LatLngTuple),
  );
}

export class GeoMap {
  private readonly map: L.Map;
  private readonly cb: GeoMapCallbacks;
  private readonly basemaps: BasemapConfig[];
  private tileLayer: L.TileLayer | null = null;
  private basemapIndex = 0;
  private dark = false;
  private readonly areaRenderer = L.svg({ padding: 0.5 });
  private areaLayer: L.Polygon | null = null;
  /** The geometry the area layer shows; the same object the app holds. */
  private areaGeometry: Polygon | null = null;
  private readonly resultsLayer = L.layerGroup();
  private readonly markers = new Map<string, L.Marker>();
  private activeResult: string | null = null;
  private mode: DrawMode = 'polygon';
  private armed = false;
  private freehand: FreehandTool;
  private pointerOnMap = false;
  private lastTileError = 0;

  constructor(container: HTMLElement, { basemaps, initialView, callbacks }: GeoMapOptions) {
    this.cb = callbacks;
    this.basemaps = basemaps;
    this.map = L.map(container, {
      center: [initialView.lat, initialView.lon],
      zoom: initialView.zoom,
      zoomControl: false, // our own controls, styled per the design
      attributionControl: false, // the attribution is rendered next to the coordinates
      worldCopyJump: true,
    });
    this.setBasemap(0);
    this.resultsLayer.addTo(this.map);
    this.areaRenderer.addTo(this.map);
    this.installGradient();

    this.map.pm.setGlobalOptions({ ...DRAW_STYLE, allowSelfIntersection: false, snappable: false });
    this.map.pm.setLang(
      'en',
      {
        tooltips: {
          firstVertex: he.draw.tooltips.firstVertex,
          continueLine: he.draw.tooltips.continueLine,
          finishPoly: he.draw.tooltips.finishPoly,
          finishRect: he.draw.tooltips.finishRect,
          startCircle: he.draw.tooltips.startCircle,
          finishCircle: he.draw.tooltips.finishCircle,
        },
      },
      'en',
    );

    this.map.on('pm:create', (e) => this.handleCreate(e.layer, e.shape));
    this.map.on('pm:drawend', () => {
      if (this.mode !== 'free' && this.mode !== 'edit') this.setArmed(false);
    });
    this.map.on('mousemove', (e: L.LeafletMouseEvent) => {
      this.pointerOnMap = true;
      this.cb.onCoords({ lat: e.latlng.lat, lon: wrapLon(e.latlng.lng) });
    });
    this.map.on('mouseout', () => {
      this.pointerOnMap = false;
      this.emitCenter();
    });
    this.map.on('moveend', () => {
      if (!this.pointerOnMap) this.emitCenter();
    });
    document.addEventListener('keydown', this.onKeyDown);

    this.freehand = new FreehandTool(this.map, (latlngs) => {
      this.setArmed(false);
      if (latlngs.length >= 3) {
        const ring = latlngs.map(toPosition);
        ring.push([...ring[0]!]);
        this.cb.onAreaDrawn({ type: 'Polygon', coordinates: [ring] }, 'free');
      }
    });
    this.emitCenter();
  }

  // ---- basemap and view ------------------------------------------------------

  get basemapCount(): number {
    return this.basemaps.length;
  }

  setBasemap(index: number): void {
    const cfg = this.basemaps[index];
    if (!cfg) return;
    this.basemapIndex = index;
    this.tileLayer?.remove();
    const useDarkTiles = this.dark && !!cfg.darkUrl;
    const dimmable = !cfg.darkUrl && cfg.darkFilter !== false;
    this.tileLayer = L.tileLayer(useDarkTiles ? cfg.darkUrl! : cfg.url, {
      attribution: cfg.attribution,
      className: dimmable ? 'gs-tiles--dimmable' : '',
      maxZoom: cfg.maxZoom ?? 19,
      ...(cfg.subdomains ? { subdomains: cfg.subdomains } : {}),
    });
    this.tileLayer.on('tileerror', () => {
      const now = Date.now();
      if (now - this.lastTileError > 10_000) {
        this.lastTileError = now;
        this.cb.onTileError();
      }
    });
    this.tileLayer.addTo(this.map);
    this.tileLayer.bringToBack();
  }

  /** Dark theme: swaps to the basemap's dark tiles, or dims light ones (CSS). */
  setDark(dark: boolean): void {
    if (dark === this.dark) return;
    this.dark = dark;
    this.map.getContainer().classList.toggle('gs-map--dark', dark);
    if (this.basemaps[this.basemapIndex]?.darkUrl) this.setBasemap(this.basemapIndex);
  }

  zoomIn(): void {
    this.map.zoomIn();
  }

  zoomOut(): void {
    this.map.zoomOut();
  }

  flyTo(position: LatLon, zoom = 16): void {
    this.map.flyTo([position.lat, position.lon], zoom, { duration: this.reducedMotion() ? 0 : 0.6 });
  }

  fitBounds(south: number, west: number, north: number, east: number): void {
    this.map.flyToBounds(
      [
        [south, west],
        [north, east],
      ],
      { padding: [24, 24], maxZoom: 17, duration: this.reducedMotion() ? 0 : 0.6 },
    );
  }

  /** Call after the container changes size (expand, panel toggle). */
  invalidateSize(): void {
    this.map.invalidateSize();
  }

  // ---- drawing -------------------------------------------------------------

  /** Selects a draw mode and arms it (the next gesture on the map draws). */
  setDrawMode(mode: DrawMode): void {
    this.disarm();
    this.mode = mode;
    if (mode === 'edit') {
      if (!this.areaLayer) return;
      this.areaLayer.pm.enable({ allowSelfIntersection: false, snappable: false });
      this.setArmed(true);
      return;
    }
    if (mode === 'free') {
      this.freehand.enable();
    } else {
      this.map.pm.enableDraw(GEOMAN_SHAPE[mode], {
        ...DRAW_STYLE,
        allowSelfIntersection: false,
        snappable: false,
        finishOn: null,
      });
    }
    this.setArmed(true);
  }

  /** Stops any drawing or editing in progress; the mode stays selected. */
  disarm(): void {
    this.map.pm.disableDraw();
    this.freehand.disable();
    if (this.areaLayer?.pm.enabled()) this.areaLayer.pm.disable();
    this.setArmed(false);
  }

  isArmed(): boolean {
    return this.armed;
  }

  private setArmed(armed: boolean): void {
    const was = this.armed;
    this.armed = armed;
    this.map.getContainer().classList.toggle('gs-map--drawing', armed);
    if (was !== armed) this.cb.onArmedChange(armed);
  }

  private handleCreate(layer: L.Layer, shape: string): void {
    // The drawn layer is only a gesture result; the area layer is rebuilt from
    // the geometry so every area looks and behaves the same.
    this.map.removeLayer(layer);
    if (shape === 'Circle' && layer instanceof L.Circle) {
      const c = layer.getLatLng();
      this.cb.onAreaDrawn(circleToPolygon({ lat: c.lat, lon: wrapLon(c.lng) }, layer.getRadius()), 'circle');
    } else if (layer instanceof L.Polygon) {
      this.cb.onAreaDrawn(layerToPolygon(layer), shape === 'Rectangle' ? 'rect' : 'polygon');
    }
  }

  private readonly onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && this.armed) {
      e.stopPropagation();
      this.disarm();
    }
  };

  // ---- the area ------------------------------------------------------------

  /** Shows the area (or none). `invalid` styles it as rejected. */
  setArea(geometry: Polygon | null, label: string | null, invalid: boolean): void {
    if (geometry && geometry === this.areaGeometry && this.areaLayer) {
      // The app echoed back an edit made here: restyle, don't rebuild mid-edit.
      this.styleArea(this.areaLayer, label, invalid);
      return;
    }
    this.areaGeometry = geometry;
    const editing = this.mode === 'edit' && this.armed;
    if (this.areaLayer) {
      this.areaLayer.pm.disable();
      this.areaLayer.remove();
      this.areaLayer = null;
    }
    if (!geometry) {
      if (editing) this.setArmed(false);
      return;
    }
    const layer = L.polygon(polygonToLatLngs(geometry), {
      ...AREA_STYLE,
      renderer: this.areaRenderer,
      className: 'gs-area',
      interactive: true,
      bubblingMouseEvents: true,
    });
    // Vertex edits report the new shape once the drag ends.
    layer.on('pm:edit', () => {
      const edited = layerToPolygon(layer);
      this.areaGeometry = edited;
      this.cb.onAreaEdited(edited);
    });
    layer.addTo(this.map);
    this.styleArea(layer, label, invalid);
    layer.bringToBack();
    this.areaLayer = layer;
    if (editing) layer.pm.enable({ allowSelfIntersection: false, snappable: false });
  }

  private styleArea(layer: L.Polygon, label: string | null, invalid: boolean): void {
    layer.getElement()?.classList.toggle('gs-area--invalid', invalid);
    const className = invalid ? 'gs-area-label gs-area-label--invalid' : 'gs-area-label';
    if (!label) {
      layer.unbindTooltip();
    } else if (layer.getTooltip()?.options.className === className) {
      layer.setTooltipContent(label);
    } else {
      layer.unbindTooltip();
      layer.bindTooltip(label, { permanent: true, direction: 'center', className, opacity: 1 });
    }
  }

  fitArea(): void {
    if (!this.areaLayer) return;
    const b = this.areaLayer.getBounds();
    this.fitBounds(b.getSouth(), b.getWest(), b.getNorth(), b.getEast());
  }

  /** The area polygon strokes with the accent gradient (solid accent if SVG is unavailable). */
  private installGradient(): void {
    const svg = (this.areaRenderer as unknown as { _container?: SVGSVGElement })._container;
    if (!svg || svg.querySelector('#gs-area-stroke')) return;
    const defs = document.createElementNS(SVG_NS, 'defs');
    const gradient = (id: string, a: string, b: string) => {
      const g = document.createElementNS(SVG_NS, 'linearGradient');
      g.setAttribute('id', id);
      g.setAttribute('x1', '0');
      g.setAttribute('y1', '0');
      g.setAttribute('x2', '1');
      g.setAttribute('y2', '1');
      for (const [offset, color] of [
        ['0', a],
        ['1', b],
      ] as const) {
        const stop = document.createElementNS(SVG_NS, 'stop');
        stop.setAttribute('offset', offset);
        stop.setAttribute('stop-color', color);
        g.appendChild(stop);
      }
      return g;
    };
    defs.appendChild(gradient('gs-area-stroke', '#5CC6F2', '#2B84C9'));
    defs.appendChild(gradient('gs-area-fill', 'rgba(92,198,242,0.22)', 'rgba(43,132,201,0.30)'));
    svg.prepend(defs);
  }

  // ---- results -------------------------------------------------------------

  setResults(results: MapResult[], fit: boolean): void {
    this.resultsLayer.clearLayers();
    this.markers.clear();
    for (const r of results) {
      const icon = L.divIcon({
        className: 'gs-marker-wrap',
        html: `<span class="gs-marker" aria-hidden="true">${r.n}</span>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      });
      const marker = L.marker([r.lat, r.lon], {
        icon,
        title: `${r.n}. ${r.name}`,
        alt: `${r.n}. ${r.name}`,
        keyboard: true,
        riseOnHover: true,
        bubblingMouseEvents: false,
      });
      marker.on('mouseover', () => this.cb.onResultHover(r.id));
      marker.on('mouseout', () => this.cb.onResultHover(null));
      marker.on('click', () => this.cb.onResultSelect(r.id));
      marker.on('add', () => {
        const el = marker.getElement();
        el?.setAttribute('aria-label', `${r.n}. ${r.name}`);
        el?.addEventListener('focus', () => this.cb.onResultHover(r.id));
        el?.addEventListener('blur', () => this.cb.onResultHover(null));
      });
      marker.addTo(this.resultsLayer);
      this.markers.set(r.id, marker);
    }
    this.applyActive();
    if (fit && results.length > 0) {
      const bounds = L.latLngBounds(results.map((r) => [r.lat, r.lon] as L.LatLngTuple));
      if (this.areaLayer) bounds.extend(this.areaLayer.getBounds());
      this.fitBounds(bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast());
    }
  }

  setActiveResult(id: string | null, pan = false): void {
    this.activeResult = id;
    this.applyActive();
    const marker = id ? this.markers.get(id) : undefined;
    if (marker && pan && !this.map.getBounds().pad(-0.1).contains(marker.getLatLng())) {
      this.map.panTo(marker.getLatLng(), { animate: !this.reducedMotion() });
    }
  }

  private applyActive(): void {
    for (const [id, marker] of this.markers) {
      const active = id === this.activeResult;
      marker.getElement()?.classList.toggle('gs-marker-wrap--active', active);
      marker.setZIndexOffset(active ? 1000 : 0);
    }
  }

  // ---- lifecycle -----------------------------------------------------------

  private emitCenter(): void {
    const c = this.map.getCenter();
    this.cb.onCoords({ lat: c.lat, lon: wrapLon(c.lng) });
  }

  private reducedMotion(): boolean {
    return globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
  }

  destroy(): void {
    document.removeEventListener('keydown', this.onKeyDown);
    this.freehand.disable();
    this.map.remove();
  }
}

/**
 * Freehand drawing (Geoman's free edition has none): press and drag with a
 * mouse, pen or finger; release to close the shape. The stroke is simplified
 * in screen space so a shaky hand doesn't produce thousands of vertices.
 */
class FreehandTool {
  private enabled = false;
  private points: L.Point[] = [];
  private preview: L.Polyline | null = null;
  private pointerId: number | null = null;

  constructor(
    private readonly map: L.Map,
    private readonly onFinish: (latlngs: L.LatLng[]) => void,
  ) {}

  enable(): void {
    if (this.enabled) return;
    this.enabled = true;
    this.map.dragging.disable();
    const el = this.map.getContainer();
    el.classList.add('gs-map--freehand');
    el.addEventListener('pointerdown', this.onDown);
  }

  disable(): void {
    if (!this.enabled) return;
    this.enabled = false;
    this.cancelStroke();
    this.map.dragging.enable();
    const el = this.map.getContainer();
    el.classList.remove('gs-map--freehand');
    el.removeEventListener('pointerdown', this.onDown);
  }

  private cancelStroke(): void {
    const el = this.map.getContainer();
    if (this.pointerId !== null && el.hasPointerCapture?.(this.pointerId)) {
      el.releasePointerCapture(this.pointerId);
    }
    el.removeEventListener('pointermove', this.onMove);
    el.removeEventListener('pointerup', this.onUp);
    el.removeEventListener('pointercancel', this.onCancel);
    this.preview?.remove();
    this.preview = null;
    this.points = [];
    this.pointerId = null;
  }

  private pointFor(e: PointerEvent): L.Point {
    return this.map.mouseEventToContainerPoint(e as unknown as MouseEvent);
  }

  private readonly onDown = (e: PointerEvent) => {
    if (e.button !== 0 || this.pointerId !== null) return;
    // Clicks on overlay controls are not drawing.
    if ((e.target as HTMLElement).closest('.gs-map-overlay')) return;
    e.preventDefault();
    const el = this.map.getContainer();
    this.pointerId = e.pointerId;
    el.setPointerCapture?.(e.pointerId);
    this.points = [this.pointFor(e)];
    this.preview = L.polyline([this.map.containerPointToLatLng(this.points[0]!)], {
      color: ACCENT,
      weight: 3,
      lineJoin: 'round',
      lineCap: 'round',
      interactive: false,
    }).addTo(this.map);
    el.addEventListener('pointermove', this.onMove);
    el.addEventListener('pointerup', this.onUp);
    el.addEventListener('pointercancel', this.onCancel);
  };

  private readonly onMove = (e: PointerEvent) => {
    if (e.pointerId !== this.pointerId) return;
    const p = this.pointFor(e);
    const last = this.points[this.points.length - 1]!;
    if (p.distanceTo(last) < 3) return;
    this.points.push(p);
    this.preview?.addLatLng(this.map.containerPointToLatLng(p));
  };

  private readonly onUp = (e: PointerEvent) => {
    if (e.pointerId !== this.pointerId) return;
    const simplified = L.LineUtil.simplify(this.points, 2);
    // A loop usually ends back on its start. Those last points are the closing
    // gesture, not vertices: kept, they'd make a zero-length or crossing edge.
    const start = simplified[0];
    while (start && simplified.length > 3 && simplified[simplified.length - 1]!.distanceTo(start) < 8) {
      simplified.pop();
    }
    const latlngs = simplified.map((p) => this.map.containerPointToLatLng(p));
    this.cancelStroke();
    this.disable();
    this.onFinish(latlngs);
  };

  private readonly onCancel = () => {
    this.cancelStroke();
  };
}
