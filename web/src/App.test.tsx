import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Polygon } from 'geojson';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { ApiError, type GeoSearchApi } from './api/client';
import type { AgentResponse, UserRequest } from './api/types';
import { AGENT_TOOLS } from './data/agentTools';
import { he } from './i18n/he';
import { AppStateProvider } from './state/AppStateProvider';
import { initialState, reducer } from './state/reducer';

// Leaflet needs a real layout engine; the map has its own module boundary (GeoMap).
vi.mock('./components/map/MapCard', () => ({ MapCard: () => <div data-testid="map" /> }));

const square: Polygon = {
  type: 'Polygon',
  coordinates: [[[34.75, 32.05], [34.8, 32.05], [34.8, 32.1], [34.75, 32.1], [34.75, 32.05]]],
};

function answer(overrides: Partial<AgentResponse> = {}): AgentResponse {
  return {
    conversation_id: 'srv-1',
    turn: 1,
    request_id: 'r',
    area_summary: 's',
    answer: 'מצאתי:\n\n- **קפה לוטוס** [i1]\n- בית הבמבוק [i2]',
    stopped_reason: 'finished',
    usage: { model_calls: 1, input_tokens: 1, output_tokens: 1, peak_input_tokens: 1, over_budget: false, summarizer_calls: 0, summarizer_input_tokens: 0 },
    items: [1, 2, 3, 4, 5, 6].map((n) => ({
      id: `i${n}`,
      source_id: 9101,
      name: `מקום ${n}`,
      lon: 34.76,
      lat: 32.06,
      data: { rating: 4.5, price_level: 'PRICE_LEVEL_MODERATE', address: `רחוב ${n}` },
    })),
    answer_source: 'submitted',
    ...overrides,
  };
}

/** An API whose answers the test releases by hand. */
function controlledApi() {
  const calls: { request: UserRequest; resolve(r: AgentResponse): void; reject(e: unknown): void }[] = [];
  const api: GeoSearchApi = {
    ask: (request) => new Promise((resolve, reject) => calls.push({ request, resolve, reject })),
    getConversation: vi.fn(),
  };
  return { api, calls };
}

function setup() {
  const { api, calls } = controlledApi();
  const state = reducer(initialState(), { type: 'AREA_DRAWN', geometry: square, kind: 'polygon', km2: 26.3 });
  render(
    <AppStateProvider api={api} initial={state}>
      <App />
    </AppStateProvider>,
  );
  return { calls, user: userEvent.setup() };
}

describe('App', () => {
  it('renders the shell in Hebrew with the tools panel closed', () => {
    setup();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('חיפוש גאוגרפי');
    expect(screen.getByText(he.chat.greeting)).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: he.chat.toolsToggle(AGENT_TOOLS.length) });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'agent-tools');
    expect(document.getElementById('agent-tools')).toBeNull();
    expect(screen.getByText(he.chat.areaChip(1, 26.3))).toBeInTheDocument();
  });

  it('sends with Enter, shows the working state, then the answer and results', async () => {
    const { calls, user } = setup();
    const box = screen.getByLabelText(he.chat.inputLabel);
    await user.type(box, 'מסעדות אסייתיות');
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    expect(box).toHaveValue('מסעדות אסייתיות\n');
    expect(calls).toHaveLength(0);

    await user.keyboard('{Enter}');
    expect(calls).toHaveLength(1);
    expect(calls[0]!.request.wkt).toMatch(/^POLYGON \(\(34\.75 32\.05/);
    expect(calls[0]!.request.conversation_id).toBeUndefined();
    expect(box).toHaveValue('');
    expect(screen.getByText(he.chat.working)).toBeInTheDocument();
    expect(screen.getByLabelText(he.chat.sendAria)).toBeDisabled();

    await act(async () => calls[0]!.resolve(answer()));
    expect(screen.queryByText(he.chat.working)).toBeNull();
    // Citations become numbered buttons; the list shows 4 of 6 until expanded.
    expect(screen.getByRole('button', { name: he.results.citeAria(1) })).toBeInTheDocument();
    const list = screen.getByRole('list', { name: he.results.aria });
    expect(within(list).getAllByRole('listitem')).toHaveLength(4);
    expect(screen.getByText(he.results.showing(4, 6))).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: he.results.all(6) }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(6);

    // The search is in history, selected.
    const item = screen.getByRole('button', { name: /מסעדות אסייתיות/ });
    expect(item).toHaveAttribute('aria-current', 'true');
    expect(screen.getByText(he.history.count(1))).toBeInTheDocument();

    // A follow-up continues the server conversation and sends no WKT.
    await user.type(box, 'ומה עם קפה?{Enter}');
    expect(calls[1]!.request).toEqual({ conversation_id: 'srv-1', prompt: 'ומה עם קפה?' });
  });

  it('shows a retryable error and retries in place', async () => {
    const { calls, user } = setup();
    await user.type(screen.getByLabelText(he.chat.inputLabel), 'שאלה{Enter}');
    await act(async () => calls[0]!.reject(new ApiError('api', 'down', { status: 503, code: 'MODEL_UNAVAILABLE' })));
    expect(screen.getByRole('alert')).toHaveTextContent(he.errors.MODEL_UNAVAILABLE);
    await user.click(screen.getByRole('button', { name: he.chat.retry }));
    expect(calls).toHaveLength(2);
    expect(calls[1]!.request.prompt).toBe('שאלה');
    await act(async () => calls[1]!.resolve(answer({ items: [], answer: 'אין תוצאות' })));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('אין תוצאות')).toBeInTheDocument();
  });

  it('sends a suggestion chip as a question', async () => {
    const { calls, user } = setup();
    await user.click(screen.getByRole('button', { name: he.chat.examples[0] }));
    expect(calls[0]!.request.prompt).toBe(he.chat.examples[0]);
  });

  it('removing the area chip disables sending', async () => {
    const { calls, user } = setup();
    await user.click(screen.getByRole('button', { name: he.chat.areaChipRemove }));
    await user.type(screen.getByLabelText(he.chat.inputLabel), 'שאלה{Enter}');
    expect(calls).toHaveLength(0);
    expect(screen.getByText(he.chat.needArea)).toBeInTheDocument();
  });

  it('opens the tools panel and shows a description only on hover or focus', async () => {
    const { user } = setup();
    const toggle = screen.getByRole('button', { name: he.chat.toolsToggle(AGENT_TOOLS.length) });
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const panel = document.getElementById('agent-tools')!;
    const tool = AGENT_TOOLS[0]!;
    const desc = within(panel).getByText(tool.description);
    expect(desc).toHaveClass('sr-only');

    const row = within(panel).getByText(tool.name).closest('li')!;
    await user.hover(row);
    expect(desc).toHaveClass('tool-row__desc');
    await user.unhover(row);
    expect(desc).toHaveClass('sr-only');

    act(() => row.focus());
    expect(desc).toHaveClass('tool-row__desc');
    await user.keyboard('{Escape}');
    expect(document.getElementById('agent-tools')).toBeNull();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('filters history by query text', async () => {
    const { calls, user } = setup();
    await user.type(screen.getByLabelText(he.chat.inputLabel), 'בתי קפה{Enter}');
    await act(async () => calls[0]!.resolve(answer()));
    const search = screen.getByLabelText(he.history.search);
    await user.type(search, 'מוזיאון');
    expect(screen.getByText(he.history.empty)).toBeInTheDocument();
    await user.clear(search);
    await user.type(search, 'קפה');
    expect(screen.getByRole('button', { name: /בתי קפה/ })).toBeInTheDocument();
  });
});
