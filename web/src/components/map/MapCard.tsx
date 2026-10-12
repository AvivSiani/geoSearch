import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Polygon } from 'geojson';
import { config } from '../../config';
import { areaKm2, findAreaIssue } from '../../geo/measure';
import type { LatLon } from '../../geo/types';
import { he } from '../../i18n/he';
import { GeoMap, type DrawMode, type MapResult } from '../../map/GeoMap';
import { useAppState, useDispatch } from '../../state/AppStateProvider';
import { useTheme } from '../../theme/theme';
import { resultsMessage } from '../../state/reducer';
import { CoordinateReadout, type CoordsSink } from './CoordinateReadout';
import { DrawToolbar } from './DrawToolbar';
import { MapControls } from './MapControls';
import { MapSearch } from './MapSearch';
import 'leaflet/dist/leaflet.css';
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';
import './MapCard.css';

export function MapCard({ expanded, onToggleExpand }: { expanded: boolean; onToggleExpand(): void }) {
  const state = useAppState();
  const dispatch = useDispatch();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<GeoMap | null>(null);
  const coordsSink = useRef<CoordsSink | null>(null);
  /** The geometry this map last reported; anything else came from history. */
  const emitted = useRef<Polygon | null>(null);
  const [mode, setMode] = useState<DrawMode>('polygon');
  const [armed, setArmed] = useState(false);
  const [basemap, setBasemap] = useState(0);
  const [tileError, setTileError] = useState(false);
  const registerCoords = useCallback((sink: CoordsSink | null) => {
    coordsSink.current = sink;
  }, []);

  // Callbacks read the latest state through a ref; the map is built once.
  const latest = useRef({ state, dispatch });
  latest.current = { state, dispatch };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const map = new GeoMap(container, {
      basemaps: config.basemaps,
      initialView: config.initialView,
      callbacks: {
        onAreaDrawn(geometry, kind) {
          emitted.current = geometry;
          latest.current.dispatch({ type: 'AREA_DRAWN', geometry, kind, km2: areaKm2(geometry) });
        },
        onAreaEdited(geometry) {
          emitted.current = geometry;
          latest.current.dispatch({ type: 'AREA_EDITED', geometry, km2: areaKm2(geometry) });
        },
        onCoords: (p: LatLon) => coordsSink.current?.(p),
        onResultHover(itemId) {
          const msg = resultsMessage(latest.current.state);
          latest.current.dispatch({
            type: 'SET_ACTIVE_ITEM',
            item: itemId && msg ? { messageId: msg.id, itemId } : null,
          });
        },
        onResultSelect(itemId) {
          const msg = resultsMessage(latest.current.state);
          if (!msg) return;
          latest.current.dispatch({ type: 'SET_ACTIVE_ITEM', item: { messageId: msg.id, itemId } });
          const row = document.getElementById(`result-${msg.id}-${itemId}`);
          row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        },
        onArmedChange: setArmed,
        onTileError() {
          setTileError(true);
        },
      },
    });
    mapRef.current = map;
    // With nothing drawn yet, the default mode is ready to draw.
    if (!latest.current.state.area) map.setDrawMode('polygon');

    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(container);
    return () => {
      observer.disconnect();
      map.destroy();
      mapRef.current = null;
    };
  }, []);

  const [theme] = useTheme();
  useEffect(() => {
    mapRef.current?.setDark(theme === 'dark');
  }, [theme]);

  // The area: drawn here, restored from history, or cleared.
  const area = state.area;
  const issue = useMemo(
    () => (area ? findAreaIssue(area.geometry, config.maxAreaKm2) : null),
    [area],
  );
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.setArea(area?.geometry ?? null, area ? he.draw.areaLabel(area.seq, area.km2) : null, issue !== null);
    if (area && area.geometry !== emitted.current) {
      emitted.current = area.geometry;
      map.fitArea();
    }
    if (!area && mode === 'edit') setMode('polygon');
  }, [area, issue, mode]);

  // Results of the focused answer, numbered like the chat list.
  const message = resultsMessage(state);
  const results = useMemo<MapResult[]>(
    () =>
      (message?.items ?? []).flatMap((item, i) =>
        item.lat !== null && item.lon !== null
          ? [{ id: item.id, n: i + 1, lat: item.lat, lon: item.lon, name: item.name ?? he.results.untitled(i + 1) }]
          : [],
      ),
    [message],
  );
  const shownMessageId = useRef<string | null>(null);
  useEffect(() => {
    const fit = (message?.id ?? null) !== shownMessageId.current;
    shownMessageId.current = message?.id ?? null;
    mapRef.current?.setResults(results, fit);
  }, [results, message]);

  const activeId = state.activeItem && state.activeItem.messageId === message?.id ? state.activeItem.itemId : null;
  useEffect(() => {
    mapRef.current?.setActiveResult(activeId, true);
  }, [activeId, results]);

  useEffect(() => {
    if (!tileError) return;
    const t = setTimeout(() => setTileError(false), 6000);
    return () => clearTimeout(t);
  }, [tileError]);

  const selectMode = (next: DrawMode) => {
    const map = mapRef.current;
    if (!map) return;
    if (next === mode && armed) {
      map.disarm();
      return;
    }
    setMode(next);
    map.setDrawMode(next);
  };

  const clearArea = () => {
    const map = mapRef.current;
    map?.disarm();
    dispatch({ type: 'AREA_CLEARED' });
    const next = mode === 'edit' ? 'polygon' : mode;
    setMode(next);
    map?.setDrawMode(next);
  };

  const cycleBasemap = () => {
    const next = (basemap + 1) % config.basemaps.length;
    setBasemap(next);
    mapRef.current?.setBasemap(next);
  };

  const basemapCfg = config.basemaps[basemap] ?? config.basemaps[0]!;

  return (
    <section className={`map-card${expanded ? ' map-card--expanded' : ''}`} aria-label={he.map.aria}>
      {/* Leaflet misbehaves in RTL containers; the map itself is LTR. */}
      <div ref={containerRef} className="map-card__map" dir="ltr" />

      <div className="gs-map-overlay map-card__search">
        <MapSearch onPick={(hit) => {
          const map = mapRef.current;
          if (!map) return;
          if (hit.bbox) map.fitBounds(...hit.bbox);
          else map.flyTo(hit.position);
        }} />
      </div>

      <div className="gs-map-overlay map-card__controls">
        <MapControls
          expanded={expanded}
          onToggleExpand={onToggleExpand}
          basemapLabel={basemapCfg.label}
          canCycleBasemap={config.basemaps.length > 1}
          onCycleBasemap={cycleBasemap}
          onZoomIn={() => mapRef.current?.zoomIn()}
          onZoomOut={() => mapRef.current?.zoomOut()}
        />
      </div>

      <div className="gs-map-overlay map-card__draw">
        <DrawToolbar
          mode={mode}
          armed={armed}
          hasArea={area !== null}
          onSelect={selectMode}
          onClear={clearArea}
        />
      </div>

      <div className="gs-map-overlay map-card__coords">
        <CoordinateReadout
          initial={{ lat: config.initialView.lat, lon: config.initialView.lon }}
          register={registerCoords}
          attribution={basemapCfg.attribution}
        />
      </div>

      {tileError && (
        <div className="gs-map-overlay map-card__notice" role="status">
          {he.map.tileError}
        </div>
      )}
    </section>
  );
}
