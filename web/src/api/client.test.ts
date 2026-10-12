import { describe, expect, it, vi } from 'vitest';
import { ApiError, createHttpApi } from './client';
import { describeError } from './describeError';
import type { AgentResponse } from './types';

const okBody: AgentResponse = {
  conversation_id: 'c1',
  turn: 1,
  request_id: 'r1',
  area_summary: 'a',
  answer: 'hi',
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
  items: [],
  answer_source: 'submitted',
};

function respond(status: number, body: unknown, raw = false) {
  return vi.fn(async () => new Response(raw ? (body as string) : JSON.stringify(body), { status }));
}

async function failure(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error('expected a failure');
}

describe('createHttpApi', () => {
  it('posts the request and returns the response', async () => {
    const fetchImpl = respond(200, okBody);
    const api = createHttpApi({ baseUrl: '', timeoutMs: 1000, fetchImpl });
    await expect(api.ask({ wkt: 'POINT(1 2)', prompt: 'q' })).resolves.toEqual(okBody);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/v1/requests');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ wkt: 'POINT(1 2)', prompt: 'q' });
  });

  it('turns an ErrorEnvelope into a typed error', async () => {
    const api = createHttpApi({
      baseUrl: 'http://x',
      timeoutMs: 1000,
      fetchImpl: respond(404, { code: 'CONVERSATION_NOT_FOUND', message: 'gone', details: { conversation_id: 'c' } }),
    });
    const err = await failure(api.ask({ conversation_id: 'c', prompt: 'q' }));
    expect(err).toMatchObject({ kind: 'api', status: 404, code: 'CONVERSATION_NOT_FOUND' });
  });

  it('treats a proxy error page as the backend being down', async () => {
    const api = createHttpApi({ baseUrl: '', timeoutMs: 1000, fetchImpl: respond(502, '<html>Bad Gateway</html>', true) });
    expect(await failure(api.ask({ prompt: 'q' }))).toMatchObject({ kind: 'network', status: 502 });
  });

  it('rejects a 200 with an unexpected body', async () => {
    const api = createHttpApi({ baseUrl: '', timeoutMs: 1000, fetchImpl: respond(200, { nope: true }) });
    expect(await failure(api.ask({ prompt: 'q' }))).toMatchObject({ kind: 'bad_response' });
  });

  it('reports network failures', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    const api = createHttpApi({ baseUrl: '', timeoutMs: 1000, fetchImpl });
    expect(await failure(api.ask({ prompt: 'q' }))).toMatchObject({ kind: 'network' });
  });

  it('times out slow turns', async () => {
    const fetchImpl = vi.fn(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const api = createHttpApi({ baseUrl: '', timeoutMs: 20, fetchImpl });
    expect(await failure(api.ask({ prompt: 'q' }))).toMatchObject({ kind: 'timeout' });
  });

  it('distinguishes a caller abort from a timeout', async () => {
    const fetchImpl = vi.fn(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const api = createHttpApi({ baseUrl: '', timeoutMs: 10_000, fetchImpl });
    const controller = new AbortController();
    const pending = api.ask({ prompt: 'q' }, controller.signal);
    controller.abort();
    expect(await failure(pending)).toMatchObject({ kind: 'aborted' });
  });

  it('encodes conversation ids in the history route', async () => {
    const fetchImpl = respond(200, { turns: [] });
    const api = createHttpApi({ baseUrl: '', timeoutMs: 1000, fetchImpl });
    await api.getConversation('a/b');
    expect((fetchImpl.mock.calls[0] as unknown as [string])[0]).toBe('/v1/conversations/a%2Fb');
  });
});

describe('describeError', () => {
  it('maps retryable backend outages', () => {
    for (const code of ['MODEL_UNAVAILABLE', 'STORE_UNAVAILABLE', 'CONVERSATION_BUSY'] as const) {
      expect(describeError(new ApiError('api', 'x', { code }))).toMatchObject({ retryable: true, action: 'none' });
    }
  });

  it('marks an expired conversation so the next send starts a new one', () => {
    expect(describeError(new ApiError('api', 'x', { code: 'CONVERSATION_NOT_FOUND' }))).toMatchObject({
      action: 'expired',
      retryable: true,
    });
  });

  it('marks the turn limit as final, with the server limit in the text', () => {
    const view = describeError(new ApiError('api', 'x', { code: 'CONVERSATION_LIMIT', details: { max_turns: 12 } }));
    expect(view).toMatchObject({ action: 'limit', retryable: false });
    expect(view.text).toContain('12');
  });

  it('never shows a raw English backend message', () => {
    const view = describeError(new ApiError('api', 'geometry is not valid', { code: 'INVALID_GEOMETRY' }));
    expect(view.text).not.toContain('geometry is not valid');
    expect(view.retryable).toBe(false);
  });

  it('handles unknown errors', () => {
    expect(describeError(new Error('boom'))).toMatchObject({ code: 'UNKNOWN', retryable: true });
  });
});
