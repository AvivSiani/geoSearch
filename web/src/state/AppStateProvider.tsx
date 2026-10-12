import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type Dispatch,
  type ReactNode,
} from 'react';
import type { GeoSearchApi } from '../api/client';
import { describeError } from '../api/describeError';
import type { UserRequest } from '../api/types';
import { config } from '../config';
import { findAreaIssue } from '../geo/measure';
import { toWkt } from '../geo/wkt';
import { he } from '../i18n/he';
import { newId } from './ids';
import { loadPersisted, savePersisted } from './persistence';
import { currentConversation, initialState, isWorking, reducer, type Action } from './reducer';
import type { AppState } from './types';
import { areaIssueText } from './areaIssueText';

export type AskResult = { ok: true } | { ok: false; reason: string };

export interface AppActions {
  /** Sends a question in the conversation on screen (or starts one). */
  ask(prompt: string): AskResult;
  /** Re-sends the prompt of a failed answer, in place. */
  retry(conversationId: string, messageId: string): void;
}

const StateContext = createContext<AppState | null>(null);
const DispatchContext = createContext<Dispatch<Action> | null>(null);
const ActionsContext = createContext<AppActions | null>(null);

function nowIso(): string {
  return new Date().toISOString();
}

/** Why a question can't be sent right now, or null if it can. */
export function blockReason(state: AppState, prompt: string): string | null {
  const conv = currentConversation(state);
  if (conv?.limitReached) return he.errors.CONVERSATION_LIMIT(undefined);
  if (isWorking(conv)) return he.errors.CONVERSATION_BUSY;
  const area = conv?.area ?? state.area;
  if (!area) return he.chat.needArea;
  if (!conv || conv.serverId === null) {
    const issue = findAreaIssue(area.geometry, config.maxAreaKm2);
    if (issue) return areaIssueText(issue);
  }
  if (prompt.trim() === '') return he.errors.MISSING_PROMPT;
  if (prompt.length > config.maxPromptChars) {
    return he.chat.promptTooLong(prompt.length, config.maxPromptChars);
  }
  return null;
}

export function AppStateProvider({
  api,
  children,
  initial,
}: {
  api: GeoSearchApi;
  children: ReactNode;
  /** Tests pass a state; the app loads history from localStorage. */
  initial?: AppState;
}) {
  const [state, dispatch] = useReducer(reducer, undefined, () => initial ?? initialState(loadPersisted()));

  // Callbacks read the latest state without re-creating themselves.
  const stateRef = useRef(state);
  stateRef.current = state;

  const controllers = useRef(new Map<string, AbortController>());
  useEffect(() => {
    const map = controllers.current;
    return () => map.forEach((c) => c.abort());
  }, []);

  // Persist history, debounced; flush when the page is hidden or closed.
  const persist = useCallback(() => {
    if (!initial) savePersisted(stateRef.current);
  }, [initial]);
  useEffect(() => {
    const timer = setTimeout(persist, 300);
    return () => clearTimeout(timer);
  }, [state.conversations, state.areaSeq, persist]);
  useEffect(() => {
    const flush = () => persist();
    window.addEventListener('pagehide', flush);
    return () => window.removeEventListener('pagehide', flush);
  }, [persist]);

  const run = useCallback(
    async (conversationId: string, messageId: string, request: UserRequest) => {
      const controller = new AbortController();
      controllers.current.set(messageId, controller);
      try {
        const response = await api.ask(request, controller.signal);
        dispatch({ type: 'SEND_SUCCESS', conversationId, messageId, response, now: nowIso() });
      } catch (err) {
        const error = describeError(err);
        dispatch({ type: 'SEND_FAILURE', conversationId, messageId, error, now: nowIso() });
      } finally {
        controllers.current.delete(messageId);
      }
    },
    [api],
  );

  const actions = useMemo<AppActions>(() => {
    const requestFor = (conversationId: string, prompt: string): UserRequest | null => {
      const conv = stateRef.current.conversations[conversationId];
      if (!conv) return null;
      return conv.serverId
        ? { conversation_id: conv.serverId, prompt }
        : { wkt: toWkt(conv.area.geometry), prompt };
    };

    return {
      ask(rawPrompt) {
        const s = stateRef.current;
        const prompt = rawPrompt.trim();
        const reason = blockReason(s, prompt);
        if (reason) return { ok: false, reason };

        const conv = currentConversation(s);
        const conversationId = conv?.id ?? newId('c');
        const request: UserRequest = conv?.serverId
          ? { conversation_id: conv.serverId, prompt }
          : { wkt: toWkt((conv?.area ?? s.area)!.geometry), prompt };
        const agentMessageId = newId('m');
        dispatch({
          type: 'SEND_START',
          conversationId,
          create: conv ? undefined : { area: s.area! },
          userMessageId: newId('m'),
          agentMessageId,
          prompt,
          now: nowIso(),
        });
        void run(conversationId, agentMessageId, request);
        return { ok: true };
      },

      retry(conversationId, messageId) {
        const conv = stateRef.current.conversations[conversationId];
        const msg = conv?.messages.find((m) => m.id === messageId);
        if (!conv || !msg || msg.role !== 'agent' || msg.status !== 'error') return;
        if (isWorking(conv) || conv.limitReached) return;
        const request = requestFor(conversationId, msg.prompt);
        if (!request) return;
        dispatch({ type: 'RETRY_START', conversationId, messageId, now: nowIso() });
        void run(conversationId, messageId, request);
      },
    };
  }, [run]);

  return (
    <StateContext.Provider value={state}>
      <DispatchContext.Provider value={dispatch}>
        <ActionsContext.Provider value={actions}>{children}</ActionsContext.Provider>
      </DispatchContext.Provider>
    </StateContext.Provider>
  );
}

function required<T>(value: T | null, name: string): T {
  if (value === null) throw new Error(`${name} must be used inside <AppStateProvider>`);
  return value;
}

export const useAppState = () => required(useContext(StateContext), 'useAppState');
export const useDispatch = () => required(useContext(DispatchContext), 'useDispatch');
export const useActions = () => required(useContext(ActionsContext), 'useActions');
