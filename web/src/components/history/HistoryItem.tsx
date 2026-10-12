import { memo } from 'react';
import { thumbnailPath } from '../../geo/thumbnail';
import { he } from '../../i18n/he';
import { isWorking, resultCount } from '../../state/reducer';
import type { Conversation } from '../../state/types';

const timeFormat = new Intl.DateTimeFormat('he-IL', { hour: '2-digit', minute: '2-digit' });

export const HistoryItem = memo(function HistoryItem({
  conversation: c,
  selected,
  onSelect,
}: {
  conversation: Conversation;
  selected: boolean;
  onSelect(id: string): void;
}) {
  const working = isWorking(c);
  return (
    <button type="button" className="history-item" aria-current={selected ? 'true' : undefined} onClick={() => onSelect(c.id)}>
      <svg className="history-item__thumb" width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">
        <path d={thumbnailPath(c.area.geometry, 46)} />
      </svg>
      <span className="history-item__text">
        <span className="history-item__title" dir="auto">
          {c.title}
        </span>
        <span className="history-item__meta">
          {timeFormat.format(new Date(c.updatedAt))} ·{' '}
          {working ? he.chat.statusWorking : he.history.results(resultCount(c))}
        </span>
      </span>
    </button>
  );
});
