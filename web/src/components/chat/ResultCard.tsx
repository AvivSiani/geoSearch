import { useEffect, useState } from 'react';
import type { ResponseItem } from '../../api/types';
import { downloadGeoJson } from '../../chat/exportResults';
import { he } from '../../i18n/he';
import { useAppState, useDispatch } from '../../state/AppStateProvider';
import { Icon } from '../Icon';

const COLLAPSED_ROWS = 4;

function meta(item: ResponseItem): string[] {
  const d = item.data;
  const parts: string[] = [];
  if (typeof d.rating === 'number') {
    parts.push(he.results.rating(d.rating, typeof d.rating_count === 'number' ? d.rating_count : undefined));
  }
  if (typeof d.price_level === 'string' && he.results.price[d.price_level]) parts.push(he.results.price[d.price_level]!);
  if (typeof d.address === 'string') parts.push(d.address);
  return parts;
}

/** Only http(s) links from tool data become anchors. */
function safeUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

export function ResultCard({
  messageId,
  items,
  onMap,
  onShowOnMap,
}: {
  messageId: string;
  items: ResponseItem[];
  /** Whether these are the results the map shows right now. */
  onMap: boolean;
  onShowOnMap(): void;
}) {
  const state = useAppState();
  const dispatch = useDispatch();
  const [expanded, setExpanded] = useState(false);
  const activeId = state.activeItem?.messageId === messageId ? state.activeItem.itemId : null;

  // A marker clicked on the map may belong to a collapsed row: reveal it.
  useEffect(() => {
    if (!activeId) return;
    const index = items.findIndex((i) => i.id === activeId);
    if (index >= COLLAPSED_ROWS) setExpanded(true);
  }, [activeId, items]);

  const shown = expanded ? items : items.slice(0, COLLAPSED_ROWS);
  const setActive = (itemId: string | null) =>
    dispatch({ type: 'SET_ACTIVE_ITEM', item: itemId ? { messageId, itemId } : null });

  return (
    <div className={`result-card${onMap ? ' result-card--on-map' : ''}`}>
      <ol className="result-card__rows" aria-label={he.results.aria}>
        {shown.map((item) => {
          const n = items.indexOf(item) + 1;
          const website = safeUrl(item.data.website);
          const phone = typeof item.data.phone === 'string' ? item.data.phone : null;
          const located = item.lat !== null && item.lon !== null;
          return (
            <li
              key={item.id}
              id={`result-${messageId}-${item.id}`}
              className={`result-row${activeId === item.id ? ' is-active' : ''}`}
              onMouseEnter={() => onMap && setActive(item.id)}
              onMouseLeave={() => onMap && setActive(null)}
            >
              <button
                type="button"
                className="result-row__main"
                onFocus={() => onMap && setActive(item.id)}
                onBlur={() => onMap && setActive(null)}
                onClick={() => {
                  if (!onMap) onShowOnMap();
                  setActive(item.id);
                }}
                disabled={!located}
              >
                <span className="result-row__badge" aria-hidden="true">
                  {n}
                </span>
                <span className="result-row__text">
                  <span className="result-row__name" dir="auto">
                    <span className="sr-only">{n}. </span>
                    {item.name ?? he.results.untitled(n)}
                  </span>
                  {meta(item).length > 0 && (
                    <span className="result-row__meta" dir="auto">
                      {meta(item).join(' · ')}
                    </span>
                  )}
                  {typeof item.data.editorial_summary === 'string' && (
                    <span className="result-row__meta" dir="auto">
                      {item.data.editorial_summary}
                    </span>
                  )}
                </span>
              </button>
              {(website || phone) && (
                <span className="result-row__links">
                  {phone && (
                    <a className="result-row__link" href={`tel:${phone.replace(/[^\d+]/g, '')}`} dir="ltr">
                      <Icon name="phone" size={14} />
                      <span className="sr-only">{he.results.phone}: </span>
                      {phone}
                    </a>
                  )}
                  {website && (
                    <a className="result-row__link" href={website} target="_blank" rel="noopener noreferrer">
                      <Icon name="globe" size={14} />
                      {he.results.website}
                    </a>
                  )}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <div className="result-card__footer">
        <span className="result-card__count">{he.results.showing(shown.length, items.length)}</span>
        <div className="result-card__actions">
          <button
            type="button"
            className="pill-btn"
            aria-label={he.results.exportAria}
            onClick={() => downloadGeoJson(items, `geosearch-results-${messageId.slice(-8)}.geojson`)}
          >
            <Icon name="download" size={14} />
            {he.results.export}
          </button>
          {items.length > COLLAPSED_ROWS && (
            <button
              type="button"
              className="pill-btn pill-btn--accent"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? he.results.less : he.results.all(items.length)}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
