import type { Polygon } from 'geojson';
import { describe, expect, it } from 'vitest';
import type { AgentResponse } from '../api/types';
import { he } from '../i18n/he';
import { blockReason } from './AppStateProvider';
import { loadPersisted, savePersisted, STORAGE_KEY, toStore } from './persistence';
import { currentConversation, initialState, isWorking, reducer, resultCount, resultsMessage, type Action } from './reducer';
import type { AppState } from './types';

const square: Polygon = {
  type: 'Polygon',
  coordinates: [[[34.75, 32.05], [34.8, 32.05], [34.8, 32.1], [34.75, 32.1], [34.75, 32.05]]],
};
const other: Polygon = {
  type: 'Polygon',
  coordinates: [[[34.7, 32.0], [34.72, 32.0], [34.72, 32.02], [34.7, 32.0]]],
};

function response(overrides: Partial<AgentResponse> = {}): AgentResponse {
  return {
    conversation_id: 'srv-1',
    turn: 1,
    request_id: 'r',
    area_summary: 's',
    answer: 'Lotus [i1]',
    stopped_reason: 'finished',
    usage: {
      model_calls: 1,
      input_tokens: 1,
      output_tokens: 1,
      peak_input_tokens: 1,
      over_budget: false,
      summarizer_calls: 0,
      summarizer_input_tokens: 0,
    },
    items: [{ id: 'i1', source_id: 9101, name: 'Lotus', lon: 34.77, lat: 32.07, data: {} }],
    answer_source: 'submitted',
    ...overrides,
  };
}

const run = (state: AppState, ...actions: Action[]) => actions.reduce(reducer, state);

function withAnswer(): AppState {
  let s = run(initialState(), { type: 'AREA_DRAWN', geometry: square, kind: 'polygon', km2: 26 });
  s = run(s, {
    type: 'SEND_START',
    conversationId: 'c1',
    create: { area: s.area! },
    userMessageId: 'u1',
    agentMessageId: 'a1',
    prompt: 'asian food',
    now: '2026-10-12T10:00:00.000Z',
  });
  return run(s, { type: 'SEND_SUCCESS', conversationId: 'c1', messageId: 'a1', response: response(), now: '2026-10-12T10:00:05.000Z' });
}

describe('reducer', () => {
  it('numbers areas and starts a conversation on the first question', () => {
    const s = withAnswer();
    expect(s.area?.seq).toBe(1);
    const conv = currentConversation(s)!;
    expect(conv).toMatchObject({ id: 'c1', serverId: 'srv-1', title: 'asian food' });
    expect(conv.messages.map((m) => m.role)).toEqual(['user', 'agent']);
    expect(conv.messages[1]).toMatchObject({ status: 'done', answer: 'Lotus [i1]' });
    expect(resultsMessage(s)?.id).toBe('a1');
    expect(resultCount(conv)).toBe(1);
  });

  it('starts a new chat (keeping the old one) when the area changes mid-conversation', () => {
    const s = run(withAnswer(), { type: 'AREA_DRAWN', geometry: other, kind: 'rect', km2: 1 });
    expect(s.currentId).toBeNull();
    expect(s.draftNotice).toBe(he.chat.areaChanged);
    expect(s.area).toMatchObject({ seq: 2, kind: 'rect' });
    expect(s.conversations.c1?.area.geometry).toBe(square);
  });

  it('treats an edit and a clear as area changes too', () => {
    expect(run(withAnswer(), { type: 'AREA_EDITED', geometry: other, km2: 1 }).currentId).toBeNull();
    const cleared = run(withAnswer(), { type: 'AREA_CLEARED' });
    expect(cleared).toMatchObject({ currentId: null, area: null });
  });

  it('restores a conversation and its area from history', () => {
    let s = run(withAnswer(), { type: 'AREA_DRAWN', geometry: other, kind: 'rect', km2: 1 });
    s = run(s, { type: 'SELECT_CONVERSATION', id: 'c1' });
    expect(s.currentId).toBe('c1');
    expect(s.area?.geometry).toBe(square);
    expect(s.draftNotice).toBeNull();
  });

  it('new chat keeps the area', () => {
    const s = run(withAnswer(), { type: 'NEW_CHAT' });
    expect(s.currentId).toBeNull();
    expect(s.area?.geometry).toBe(square);
  });

  it('forgets the server conversation when it expired', () => {
    let s = withAnswer();
    s = run(s, {
      type: 'SEND_START',
      conversationId: 'c1',
      userMessageId: 'u2',
      agentMessageId: 'a2',
      prompt: 'more',
      now: '2026-10-12T11:00:00.000Z',
    });
    expect(isWorking(currentConversation(s))).toBe(true);
    s = run(s, {
      type: 'SEND_FAILURE',
      conversationId: 'c1',
      messageId: 'a2',
      now: '2026-10-12T11:00:01.000Z',
      error: { text: 'x', code: 'CONVERSATION_NOT_FOUND', retryable: true, action: 'expired' },
    });
    expect(currentConversation(s)?.serverId).toBeNull();
    expect(isWorking(currentConversation(s))).toBe(false);
    // Retrying puts the same message back to working.
    s = run(s, { type: 'RETRY_START', conversationId: 'c1', messageId: 'a2', now: '2026-10-12T11:00:02.000Z' });
    expect(currentConversation(s)?.messages.at(-1)).toMatchObject({ id: 'a2', status: 'working', prompt: 'more' });
  });

  it('locks a conversation at the turn limit', () => {
    let s = run(withAnswer(), {
      type: 'SEND_START',
      conversationId: 'c1',
      userMessageId: 'u2',
      agentMessageId: 'a2',
      prompt: 'more',
      now: 'n',
    });
    s = run(s, {
      type: 'SEND_FAILURE',
      conversationId: 'c1',
      messageId: 'a2',
      now: 'n',
      error: { text: 'x', code: 'CONVERSATION_LIMIT', retryable: false, action: 'limit' },
    });
    expect(currentConversation(s)?.limitReached).toBe(true);
    expect(blockReason(s, 'hello')).toContain('20');
  });

  it('applies a late answer to its own conversation, not the one on screen', () => {
    let s = run(initialState(), { type: 'AREA_DRAWN', geometry: square, kind: 'polygon', km2: 26 });
    s = run(s, { type: 'SEND_START', conversationId: 'c1', create: { area: s.area! }, userMessageId: 'u1', agentMessageId: 'a1', prompt: 'q', now: 'n' });
    s = run(s, { type: 'NEW_CHAT' });
    s = run(s, { type: 'SEND_SUCCESS', conversationId: 'c1', messageId: 'a1', response: response(), now: 'n' });
    expect(s.currentId).toBeNull();
    expect(s.conversations.c1?.messages[1]).toMatchObject({ status: 'done' });
  });

  it('lets an older answer take over the map when focused', () => {
    let s = withAnswer();
    s = run(
      s,
      { type: 'SEND_START', conversationId: 'c1', userMessageId: 'u2', agentMessageId: 'a2', prompt: 'more', now: 'n' },
      { type: 'SEND_SUCCESS', conversationId: 'c1', messageId: 'a2', response: response({ items: [{ id: 'i2', source_id: 9101, name: 'B', lon: 34.76, lat: 32.06, data: {} }] }), now: 'n' },
    );
    expect(resultsMessage(s)?.id).toBe('a2');
    s = run(s, { type: 'FOCUS_MESSAGE', messageId: 'a1' });
    expect(resultsMessage(s)?.id).toBe('a1');
  });
});

describe('blockReason', () => {
  it('needs an area for a new chat', () => {
    expect(blockReason(initialState(), 'q')).toBe(he.chat.needArea);
  });

  it('rejects invalid areas before sending', () => {
    const bowtie: Polygon = { type: 'Polygon', coordinates: [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]] };
    const s = run(initialState(), { type: 'AREA_DRAWN', geometry: bowtie, kind: 'free', km2: 1 });
    expect(blockReason(s, 'q')).toBe(he.area.selfIntersects);
  });

  it('rejects empty and overlong prompts', () => {
    const s = run(initialState(), { type: 'AREA_DRAWN', geometry: square, kind: 'polygon', km2: 26 });
    expect(blockReason(s, '  ')).toBe(he.errors.MISSING_PROMPT);
    expect(blockReason(s, 'x'.repeat(2001))).toContain('2001');
    expect(blockReason(s, 'ok')).toBeNull();
  });
});

describe('persistence', () => {
  it('round-trips history and settles turns that were running', () => {
    let s = run(initialState(), { type: 'AREA_DRAWN', geometry: square, kind: 'polygon', km2: 26 });
    s = run(s, { type: 'SEND_START', conversationId: 'c1', create: { area: s.area! }, userMessageId: 'u1', agentMessageId: 'a1', prompt: 'q', now: 'n' });
    expect(savePersisted(s, localStorage)).toBe(true);
    const loaded = loadPersisted(localStorage);
    expect(loaded.areaSeq).toBe(1);
    expect(loaded.conversations.c1?.messages[1]).toMatchObject({ status: 'error', error: { retryable: true } });
  });

  it('ignores corrupt or foreign data', () => {
    localStorage.setItem(STORAGE_KEY, '{not json');
    expect(loadPersisted(localStorage)).toEqual({ conversations: {}, areaSeq: 0 });
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 99, conversations: [] }));
    expect(loadPersisted(localStorage).conversations).toEqual({});
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, areaSeq: 3, conversations: [{ id: 'x' }] }));
    expect(loadPersisted(localStorage)).toEqual({ conversations: {}, areaSeq: 3 });
  });

  it('drops the oldest conversations when storage is full', () => {
    const s = withAnswer();
    const conversations = { ...s.conversations };
    for (let i = 0; i < 10; i++) {
      conversations[`old${i}`] = { ...s.conversations.c1!, id: `old${i}`, updatedAt: `2020-01-0${i}` };
    }
    let writes = 0;
    const tiny = {
      setItem(_k: string, v: string) {
        writes += 1;
        if (JSON.parse(v).conversations.length > 3) throw new DOMException('full', 'QuotaExceededError');
      },
    } as unknown as Storage;
    expect(savePersisted({ conversations, areaSeq: 1 }, tiny)).toBe(true);
    expect(writes).toBeGreaterThan(1);
  });

  it('stores newest first and skips empty conversations', () => {
    const s = withAnswer();
    const empty = { ...s.conversations.c1!, id: 'e', messages: [] };
    const stored = toStore({ conversations: { ...s.conversations, e: empty }, areaSeq: 1 });
    expect(stored.conversations.map((c) => c.id)).toEqual(['c1']);
  });
});
