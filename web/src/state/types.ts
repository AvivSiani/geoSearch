import type { ErrorView } from '../api/describeError';
import type { ResponseItem } from '../api/types';
import type { Area } from '../geo/types';

export interface UserMessage {
  id: string;
  role: 'user';
  text: string;
  at: string;
}

export interface AgentMessage {
  id: string;
  role: 'agent';
  at: string;
  status: 'working' | 'done' | 'error';
  /** The prompt this message answers; a retry sends it again. */
  prompt: string;
  answer?: string;
  items?: ResponseItem[];
  answerSource?: 'submitted' | 'fallback';
  stoppedReason?: 'finished' | 'call_limit';
  turn?: number;
  error?: ErrorView;
}

export interface NoticeMessage {
  id: string;
  role: 'notice';
  text: string;
  at: string;
}

export type ChatMessage = UserMessage | AgentMessage | NoticeMessage;

/**
 * One conversation as the browser remembers it. The backend keeps only text
 * turns (no items, no geometry), so this local copy is what history restores.
 */
export interface Conversation {
  /** Local id; stable even when the server conversation is replaced. */
  id: string;
  /** The backend's conversation_id. null until the first answer, and again
   *  after the server copy expired (the next send starts a new one). */
  serverId: string | null;
  area: Area;
  title: string;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
  limitReached: boolean;
}

export interface AppState {
  conversations: Record<string, Conversation>;
  /** The conversation on screen; null shows the greeting (a new chat). */
  currentId: string | null;
  /** A notice shown on the greeting screen (e.g. "the area changed"). */
  draftNotice: string | null;
  /** The area on the map, which the next question is scoped to. */
  area: Area | null;
  /** Last "מצולע N" number handed out. */
  areaSeq: number;
  /** Whose results the map shows; null = the latest answer with results. */
  focusedMessageId: string | null;
  /** Hovered/focused result, highlighted on the map and in the list. */
  activeItem: { messageId: string; itemId: string } | null;
  toolsOpen: boolean;
}
