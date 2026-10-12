import type { ErrorView } from '../api/describeError';
import type { AgentResponse } from '../api/types';
import { he } from '../i18n/he';
import type { Area, AreaKind } from '../geo/types';
import type { Polygon } from 'geojson';
import type { AgentMessage, AppState, ChatMessage, Conversation } from './types';

export type Action =
  | { type: 'AREA_DRAWN'; geometry: Polygon; kind: AreaKind; km2: number }
  | { type: 'AREA_EDITED'; geometry: Polygon; km2: number }
  | { type: 'AREA_CLEARED' }
  | { type: 'SELECT_CONVERSATION'; id: string }
  | { type: 'NEW_CHAT' }
  | {
      type: 'SEND_START';
      conversationId: string;
      /** Set when this send creates the conversation (first question). */
      create?: { area: Area };
      userMessageId: string;
      agentMessageId: string;
      prompt: string;
      now: string;
    }
  | { type: 'RETRY_START'; conversationId: string; messageId: string; now: string }
  | {
      type: 'SEND_SUCCESS';
      conversationId: string;
      messageId: string;
      response: AgentResponse;
      now: string;
    }
  | { type: 'SEND_FAILURE'; conversationId: string; messageId: string; error: ErrorView; now: string }
  | { type: 'FOCUS_MESSAGE'; messageId: string | null }
  | { type: 'SET_ACTIVE_ITEM'; item: AppState['activeItem'] }
  | { type: 'SET_TOOLS_OPEN'; open: boolean };

export function initialState(partial: Partial<AppState> = {}): AppState {
  return {
    conversations: {},
    currentId: null,
    draftNotice: null,
    area: null,
    areaSeq: 0,
    focusedMessageId: null,
    activeItem: null,
    toolsOpen: false,
    ...partial,
  };
}

function updateConversation(
  state: AppState,
  id: string,
  fn: (c: Conversation) => Conversation,
): AppState {
  const conv = state.conversations[id];
  if (!conv) return state;
  return { ...state, conversations: { ...state.conversations, [id]: fn(conv) } };
}

function updateAgentMessage(
  conv: Conversation,
  messageId: string,
  fn: (m: AgentMessage) => AgentMessage,
): ChatMessage[] {
  return conv.messages.map((m) => (m.id === messageId && m.role === 'agent' ? fn(m) : m));
}

/**
 * The area changed. One conversation is bound to one area on the backend, so a
 * conversation with questions in it is left (it stays in history) and the chat
 * returns to the greeting with a notice.
 */
function withNewArea(state: AppState, area: Area | null): AppState {
  const current = state.currentId ? state.conversations[state.currentId] : undefined;
  const leaving = current !== undefined && current.messages.length > 0;
  return {
    ...state,
    area,
    currentId: leaving ? null : state.currentId,
    draftNotice: leaving ? he.chat.areaChanged : state.draftNotice,
    focusedMessageId: leaving ? null : state.focusedMessageId,
    activeItem: leaving ? null : state.activeItem,
  };
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'AREA_DRAWN': {
      const seq = state.areaSeq + 1;
      const area: Area = { geometry: action.geometry, kind: action.kind, km2: action.km2, seq };
      return { ...withNewArea(state, area), areaSeq: seq };
    }

    case 'AREA_EDITED': {
      if (!state.area) return state;
      return withNewArea(state, { ...state.area, geometry: action.geometry, km2: action.km2 });
    }

    case 'AREA_CLEARED':
      return state.area ? withNewArea(state, null) : state;

    case 'SELECT_CONVERSATION': {
      const conv = state.conversations[action.id];
      if (!conv) return state;
      return {
        ...state,
        currentId: conv.id,
        area: conv.area,
        draftNotice: null,
        focusedMessageId: null,
        activeItem: null,
      };
    }

    case 'NEW_CHAT':
      return { ...state, currentId: null, draftNotice: null, focusedMessageId: null, activeItem: null };

    case 'SEND_START': {
      const user: ChatMessage = {
        id: action.userMessageId,
        role: 'user',
        text: action.prompt,
        at: action.now,
      };
      const agent: AgentMessage = {
        id: action.agentMessageId,
        role: 'agent',
        status: 'working',
        prompt: action.prompt,
        at: action.now,
      };
      let next = state;
      if (action.create) {
        const conv: Conversation = {
          id: action.conversationId,
          serverId: null,
          area: action.create.area,
          title: action.prompt,
          // The greeting's notice, if any, is not part of the new conversation.
          messages: [],
          createdAt: action.now,
          updatedAt: action.now,
          limitReached: false,
        };
        next = {
          ...state,
          conversations: { ...state.conversations, [conv.id]: conv },
          currentId: conv.id,
          draftNotice: null,
        };
      }
      return {
        ...updateConversation(next, action.conversationId, (c) => ({
          ...c,
          messages: [...c.messages, user, agent],
          updatedAt: action.now,
        })),
        focusedMessageId: null,
      };
    }

    case 'RETRY_START':
      return updateConversation(state, action.conversationId, (c) => ({
        ...c,
        updatedAt: action.now,
        messages: updateAgentMessage(c, action.messageId, (m) => ({
          id: m.id,
          role: 'agent',
          prompt: m.prompt,
          status: 'working',
          at: action.now,
        })),
      }));

    case 'SEND_SUCCESS': {
      const r = action.response;
      const next = updateConversation(state, action.conversationId, (c) => ({
        ...c,
        serverId: r.conversation_id,
        updatedAt: action.now,
        messages: updateAgentMessage(c, action.messageId, (m) => ({
          ...m,
          status: 'done',
          at: action.now,
          answer: r.answer,
          items: r.items,
          answerSource: r.answer_source,
          stoppedReason: r.stopped_reason,
          turn: r.turn,
          error: undefined,
        })),
      }));
      // A new answer takes over the map only in the conversation on screen.
      return action.conversationId === state.currentId
        ? { ...next, focusedMessageId: null, activeItem: null }
        : next;
    }

    case 'SEND_FAILURE':
      return updateConversation(state, action.conversationId, (c) => ({
        ...c,
        updatedAt: action.now,
        serverId: action.error.action === 'expired' ? null : c.serverId,
        limitReached: c.limitReached || action.error.action === 'limit',
        messages: updateAgentMessage(c, action.messageId, (m) => ({
          ...m,
          status: 'error',
          at: action.now,
          error: action.error,
        })),
      }));

    case 'FOCUS_MESSAGE':
      return { ...state, focusedMessageId: action.messageId, activeItem: null };

    case 'SET_ACTIVE_ITEM':
      return { ...state, activeItem: action.item };

    case 'SET_TOOLS_OPEN':
      return { ...state, toolsOpen: action.open };
  }
}

// ---- selectors ---------------------------------------------------------------

export function currentConversation(state: AppState): Conversation | null {
  return state.currentId ? (state.conversations[state.currentId] ?? null) : null;
}

export function isWorking(conv: Conversation | null): boolean {
  return conv?.messages.some((m) => m.role === 'agent' && m.status === 'working') ?? false;
}

/** The answer whose results the map shows. */
export function resultsMessage(state: AppState): AgentMessage | null {
  const conv = currentConversation(state);
  if (!conv) return null;
  const answers = conv.messages.filter(
    (m): m is AgentMessage => m.role === 'agent' && m.status === 'done' && (m.items?.length ?? 0) > 0,
  );
  if (state.focusedMessageId) {
    const focused = answers.find((m) => m.id === state.focusedMessageId);
    if (focused) return focused;
  }
  return answers[answers.length - 1] ?? null;
}

/** Distinct results found across a conversation (the history row's count). */
export function resultCount(conv: Conversation): number {
  const ids = new Set<string>();
  for (const m of conv.messages) {
    if (m.role === 'agent') for (const item of m.items ?? []) ids.add(item.id);
  }
  return ids.size;
}
