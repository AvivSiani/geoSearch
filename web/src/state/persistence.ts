import { he } from '../i18n/he';
import type { AppState, ChatMessage, Conversation } from './types';

/**
 * Search history lives in this browser's localStorage: the backend keeps only
 * text turns (no items, no area geometry) and has no "list conversations"
 * route. Bump STORAGE_VERSION and add a migration when the shape changes.
 */
export const STORAGE_KEY = 'geosearch.history';
export const STORAGE_VERSION = 1;
export const MAX_CONVERSATIONS = 100;

interface StoredV1 {
  version: 1;
  areaSeq: number;
  conversations: Conversation[];
}

export type Persisted = Pick<AppState, 'conversations' | 'areaSeq'>;

function getStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null; // blocked site data, sandboxed frames
  }
}

function isConversation(c: unknown): c is Conversation {
  const v = c as Conversation;
  return (
    typeof v === 'object' &&
    v !== null &&
    typeof v.id === 'string' &&
    typeof v.title === 'string' &&
    Array.isArray(v.messages) &&
    typeof v.area?.geometry === 'object' &&
    v.area.geometry?.type === 'Polygon' &&
    Array.isArray(v.area.geometry.coordinates)
  );
}

/** A turn that was running when the page closed lost its response for good. */
function settle(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((m) =>
    m.role === 'agent' && m.status === 'working'
      ? {
          ...m,
          status: 'error',
          error: { text: he.chat.interrupted, code: 'ABORTED', retryable: true, action: 'none' },
        }
      : m,
  );
}

export function loadPersisted(storage: Storage | null = getStorage()): Persisted {
  const empty: Persisted = { conversations: {}, areaSeq: 0 };
  if (!storage) return empty;
  let raw: string | null;
  try {
    raw = storage.getItem(STORAGE_KEY);
  } catch {
    return empty;
  }
  if (!raw) return empty;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredV1>;
    if (parsed.version !== STORAGE_VERSION || !Array.isArray(parsed.conversations)) return empty;
    const conversations: Record<string, Conversation> = {};
    for (const c of parsed.conversations.filter(isConversation)) {
      conversations[c.id] = { ...c, limitReached: !!c.limitReached, messages: settle(c.messages) };
    }
    const maxSeq = Math.max(0, ...Object.values(conversations).map((c) => c.area.seq || 0));
    return {
      conversations,
      areaSeq: Math.max(typeof parsed.areaSeq === 'number' ? parsed.areaSeq : 0, maxSeq),
    };
  } catch (err) {
    console.warn('Ignoring unreadable search history:', err);
    return empty;
  }
}

/** Newest first, capped. Conversations with no question yet are never stored. */
export function toStore(state: Persisted): StoredV1 {
  const list = Object.values(state.conversations)
    .filter((c) => c.messages.some((m) => m.role === 'user'))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, MAX_CONVERSATIONS);
  return { version: 1, areaSeq: state.areaSeq, conversations: list };
}

/**
 * Writes the history; when the quota is full, drops the oldest conversations
 * until it fits. Returns false if nothing could be written.
 */
export function savePersisted(state: Persisted, storage: Storage | null = getStorage()): boolean {
  if (!storage) return false;
  const data = toStore(state);
  while (true) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(data));
      return true;
    } catch (err) {
      if (data.conversations.length === 0) {
        console.warn('Could not save search history:', err);
        return false;
      }
      data.conversations = data.conversations.slice(0, Math.floor(data.conversations.length / 2));
    }
  }
}
