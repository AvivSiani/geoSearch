import { useCallback, useId, useMemo, useState } from 'react';
import { he } from '../../i18n/he';
import { useAppState, useDispatch } from '../../state/AppStateProvider';
import { Icon } from '../Icon';
import { groupHistory } from './groupHistory';
import { HistoryItem } from './HistoryItem';
import './HistoryPanel.css';

export function HistoryPanel() {
  const state = useAppState();
  const dispatch = useDispatch();
  const [query, setQuery] = useState('');
  const searchId = useId();
  const titleId = useId();

  const all = useMemo(
    () => Object.values(state.conversations).filter((c) => c.messages.some((m) => m.role === 'user')),
    [state.conversations],
  );
  const groups = useMemo(() => groupHistory(all, query), [all, query]);
  // Stable, so unchanged rows skip re-rendering while a turn is running.
  const select = useCallback((id: string) => dispatch({ type: 'SELECT_CONVERSATION', id }), [dispatch]);

  return (
    <aside className="history" aria-labelledby={titleId}>
      <div className="history__header">
        <div className="history__title-row">
          <Icon name="clock" size={18} className="history__icon" />
          <h2 id={titleId} className="history__title">
            {he.history.title}
          </h2>
          <span className="history__count">{he.history.count(all.length)}</span>
        </div>
        <div className="history__search">
          <Icon name="search" size={16} className="history__search-icon" />
          <label htmlFor={searchId} className="sr-only">
            {he.history.search}
          </label>
          <input
            id={searchId}
            type="search"
            className="history__search-input"
            placeholder={he.history.search}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <nav className="history__list" aria-label={he.history.listAria}>
        {all.length === 0 ? (
          <p className="history__empty">{he.history.none}</p>
        ) : groups.length === 0 ? (
          <p className="history__empty" role="status">
            {he.history.empty}
          </p>
        ) : (
          groups.map((g) => (
            <section key={g.key} className="history__group" aria-label={g.label}>
              <h3 className="history__group-label">{g.label}</h3>
              <ul className="history__items">
                {g.conversations.map((c) => (
                  <li key={c.id}>
                    <HistoryItem
                      conversation={c}
                      selected={c.id === state.currentId}
                      onSelect={select}
                    />
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </nav>
    </aside>
  );
}
