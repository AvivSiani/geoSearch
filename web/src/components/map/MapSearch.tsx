import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { config } from '../../config';
import { he } from '../../i18n/he';
import { coordinateHit, createGeocoder, type GeocodeResult, type Geocoder } from '../../map/geocoder';
import { Icon } from '../Icon';

type Status = 'idle' | 'loading' | 'done' | 'error';

/**
 * Place or coordinate search, as an ARIA combobox. Typed coordinates resolve
 * instantly; names go to the configured geocoder (debounced, and the previous
 * request is cancelled when the text changes).
 */
export function MapSearch({
  onPick,
  geocoder: injected,
}: {
  onPick(hit: GeocodeResult): void;
  geocoder?: Geocoder | null;
}) {
  const geocoder = useMemo(() => (injected !== undefined ? injected : createGeocoder(config)), [injected]);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<GeocodeResult[]>([]);
  const [status, setStatus] = useState<Status>('idle');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const q = query.trim();
    abortRef.current?.abort();
    if (q.length < 2) {
      setHits([]);
      setStatus('idle');
      return;
    }
    const coords = coordinateHit(q);
    if (coords || !geocoder) {
      setHits(coords ? [coords] : []);
      setStatus('done');
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const timer = setTimeout(async () => {
      setStatus('loading');
      try {
        const found = await geocoder.search(q, { signal: controller.signal, near: config.initialView });
        if (controller.signal.aborted) return;
        setHits(found);
        setStatus('done');
        setActive(found.length ? 0 : -1);
      } catch {
        if (!controller.signal.aborted) setStatus('error');
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, geocoder]);

  const pick = (hit: GeocodeResult) => {
    onPick(hit);
    setQuery(hit.label);
    setOpen(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (hits.length ? (i + 1) % hits.length : -1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (hits.length ? (i - 1 + hits.length) % hits.length : -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const hit = hits[active] ?? hits[0];
      if (hit) pick(hit);
    } else if (e.key === 'Escape') {
      if (open) {
        e.stopPropagation();
        setOpen(false);
      } else {
        setQuery('');
      }
    }
  };

  const showList = open && query.trim().length >= 2 && status !== 'idle';
  const optionId = (i: number) => `${listId}-opt-${i}`;

  return (
    <div className="map-search">
      <Icon name="search" size={16} className="map-search__icon" />
      <input
        ref={inputRef}
        className="map-search__input"
        type="search"
        // Typed coordinates are LTR, names RTL; empty keeps the RTL placeholder.
        dir={query ? 'auto' : undefined}
        role="combobox"
        aria-label={he.map.search}
        placeholder={he.map.search}
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && active >= 0 && hits[active] ? optionId(active) : undefined}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        autoComplete="off"
        spellCheck={false}
      />
      <div className="map-search__popup" hidden={!showList}>
        {status === 'loading' && <p className="map-search__status">{he.map.searching}</p>}
        {status === 'error' && <p className="map-search__status">{he.map.searchError}</p>}
        {status === 'done' && hits.length === 0 && <p className="map-search__status">{he.map.searchNoResults}</p>}
        <ul id={listId} role="listbox" className="map-search__list" aria-label={he.map.search}>
          {status === 'done' &&
            hits.map((hit, i) => (
              <li
                key={hit.id}
                id={optionId(i)}
                role="option"
                aria-selected={i === active}
                className={`map-search__option${i === active ? ' is-active' : ''}`}
                // mousedown, not click: the input's blur would close the list first.
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(hit);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <span className="map-search__label" dir="auto">
                  {hit.id === 'coords' ? <span className="mono" dir="ltr">{hit.label}</span> : hit.label}
                </span>
                {hit.detail && <span className="map-search__detail" dir="auto">{hit.detail}</span>}
              </li>
            ))}
        </ul>
      </div>
    </div>
  );
}
