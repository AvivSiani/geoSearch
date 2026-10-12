import type { AgentResponse, ConversationHistory, ErrorCode, ErrorEnvelope, UserRequest } from './types';

/**
 * Why a call failed. `api` carries the backend's ErrorEnvelope; the others are
 * transport problems the backend never saw (or never answered).
 */
export type ApiErrorKind = 'api' | 'network' | 'timeout' | 'aborted' | 'bad_response';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: ErrorCode | null;
  readonly details: Record<string, unknown>;

  constructor(
    kind: ApiErrorKind,
    message: string,
    opts: { status?: number; code?: ErrorCode; details?: Record<string, unknown> } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = opts.status ?? null;
    this.code = opts.code ?? null;
    this.details = opts.details ?? {};
  }
}

/** The two backend routes, behind an interface so a mock can stand in. */
export interface GeoSearchApi {
  ask(request: UserRequest, signal?: AbortSignal): Promise<AgentResponse>;
  getConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationHistory>;
}

function isEnvelope(body: unknown): body is ErrorEnvelope {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as ErrorEnvelope).code === 'string' &&
    typeof (body as ErrorEnvelope).message === 'string'
  );
}

function isAgentResponse(body: unknown): body is AgentResponse {
  const r = body as AgentResponse;
  return (
    typeof r === 'object' &&
    r !== null &&
    typeof r.conversation_id === 'string' &&
    typeof r.answer === 'string' &&
    typeof r.turn === 'number'
  );
}

/**
 * Combines the caller's abort signal with a timeout. Returns the merged signal
 * and a function that tells whether the timeout (not the caller) fired.
 */
function withTimeout(signal: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    dispose: () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    },
  };
}

export interface HttpApiOptions {
  baseUrl: string;
  timeoutMs: number;
  fetchImpl?: typeof fetch;
}

export function createHttpApi({ baseUrl, timeoutMs, fetchImpl }: HttpApiOptions): GeoSearchApi {
  const doFetch = fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));

  async function call<T>(
    path: string,
    init: RequestInit,
    signal: AbortSignal | undefined,
    validate: (body: unknown) => body is T,
  ): Promise<T> {
    const t = withTimeout(signal, timeoutMs);
    let response: Response;
    try {
      response = await doFetch(`${baseUrl}${path}`, { ...init, signal: t.signal });
    } catch (err) {
      t.dispose();
      if (t.timedOut()) throw new ApiError('timeout', 'request timed out');
      if (signal?.aborted) throw new ApiError('aborted', 'request aborted');
      throw new ApiError('network', err instanceof Error ? err.message : 'network error');
    }

    let body: unknown = null;
    try {
      const text = await response.text();
      body = text ? JSON.parse(text) : null;
    } catch {
      // Not JSON (e.g. a proxy's HTML error page); handled below.
    } finally {
      t.dispose();
    }

    if (!response.ok) {
      if (isEnvelope(body)) {
        throw new ApiError('api', body.message, {
          status: response.status,
          code: body.code,
          details: body.details ?? {},
        });
      }
      // A proxy with no backend behind it answers 502/504 without an envelope.
      const kind = response.status >= 500 ? 'network' : 'bad_response';
      throw new ApiError(kind, `HTTP ${response.status}`, { status: response.status });
    }
    if (!validate(body)) {
      throw new ApiError('bad_response', 'unexpected response shape', { status: response.status });
    }
    return body;
  }

  return {
    ask(request, signal) {
      return call(
        '/v1/requests',
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(request),
        },
        signal,
        isAgentResponse,
      );
    },
    getConversation(conversationId, signal) {
      return call(
        `/v1/conversations/${encodeURIComponent(conversationId)}`,
        { method: 'GET' },
        signal,
        (b: unknown): b is ConversationHistory =>
          typeof b === 'object' && b !== null && Array.isArray((b as ConversationHistory).turns),
      );
    },
  };
}
