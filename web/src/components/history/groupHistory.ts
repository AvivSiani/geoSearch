import { he } from '../../i18n/he';
import type { Conversation } from '../../state/types';

export interface HistoryGroup {
  key: string;
  label: string;
  conversations: Conversation[];
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

const dateLabel = new Intl.DateTimeFormat('he-IL', { day: 'numeric', month: 'long', year: 'numeric' });

/** Newest first, grouped by local day: היום, אתמול, then dates. */
export function groupHistory(conversations: Conversation[], query: string, now = new Date()): HistoryGroup[] {
  const q = query.trim().toLocaleLowerCase();
  const matches = (c: Conversation) =>
    q === '' ||
    c.title.toLocaleLowerCase().includes(q) ||
    c.messages.some((m) => m.role === 'user' && m.text.toLocaleLowerCase().includes(q));

  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const groups = new Map<string, HistoryGroup>();
  const sorted = conversations.filter(matches).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  for (const c of sorted) {
    const d = new Date(c.updatedAt);
    const key = dayKey(d);
    let group = groups.get(key);
    if (!group) {
      const label = key === today ? he.history.today : key === yesterday ? he.history.yesterday : dateLabel.format(d);
      group = { key, label, conversations: [] };
      groups.set(key, group);
    }
    group.conversations.push(c);
  }
  return [...groups.values()];
}
