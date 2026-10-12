import type { Polygon, Position } from 'geojson';
import { ApiError, type GeoSearchApi } from './client';
import type { AgentResponse, ConversationHistory, ResponseItem } from './types';

/**
 * An in-browser stand-in for the backend (`npm run dev:mock`), for working on
 * the UI without a model or MongoDB. It follows the real contract: WKT starts a
 * conversation, conversation_id continues it, items carry [iN] citations.
 *
 * Prompts containing "#error" fail with MODEL_UNAVAILABLE, and "#busy" with
 * CONVERSATION_BUSY, to exercise the error states.
 */

const NAMES_HE = ['קפה לוטוס', 'בית הבמבוק', 'פינת סייגון', 'נודלס בר', 'גן הסאקורה', 'מסעדת המזרח', 'ראמן תל אביב', 'קפה הים'];
const NAMES_EN = ['Lotus Noodle Bar', 'Bamboo House', 'Saigon Corner', 'Sakura Garden', 'Wok Station', 'Ramen Ya', 'Dim Sum Lab', 'Pho Real'];
const PRICES = ['PRICE_LEVEL_INEXPENSIVE', 'PRICE_LEVEL_MODERATE', 'PRICE_LEVEL_EXPENSIVE'];

function parseWktPolygon(wkt: string): Polygon {
  const body = /POLYGON\s*\(\((.+?)\)/i.exec(wkt)?.[1];
  if (!body) throw new ApiError('api', 'could not parse wkt', { status: 422, code: 'WKT_PARSE_ERROR' });
  const ring: Position[] = body.split(',').map((pair) => pair.trim().split(/\s+/).map(Number));
  return { type: 'Polygon', coordinates: [ring] };
}

function inside([x, y]: Position, ring: Position[]): boolean {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]!;
    const [xj, yj] = ring[j]!;
    if (yi! > y! !== yj! > y! && x! < ((xj! - xi!) * (y! - yi!)) / (yj! - yi!) + xi!) hit = !hit;
  }
  return hit;
}

function randomPoints(poly: Polygon, count: number, seed: number): Position[] {
  const ring = poly.coordinates[0]!;
  const xs = ring.map((p) => p[0]!);
  const ys = ring.map((p) => p[1]!);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  let s = seed;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const out: Position[] = [];
  for (let tries = 0; out.length < count && tries < count * 200; tries++) {
    const p = [minX + rand() * (maxX - minX), minY + rand() * (maxY - minY)];
    if (inside(p, ring)) out.push(p);
  }
  return out;
}

interface MockConversation {
  area: Polygon;
  turns: { prompt: string; answer: string; items: string[]; at: string }[];
  createdAt: string;
  nextItem: number;
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(t);
      reject(new ApiError('aborted', 'aborted'));
    });
  });

export function createMockApi(delayMs = 1400): GeoSearchApi {
  const conversations = new Map<string, MockConversation>();

  return {
    async ask(request, signal) {
      await sleep(delayMs, signal);
      if (request.prompt.includes('#error')) {
        throw new ApiError('api', 'the model server is unreachable', { status: 503, code: 'MODEL_UNAVAILABLE' });
      }
      if (request.prompt.includes('#busy')) {
        throw new ApiError('api', 'busy', { status: 409, code: 'CONVERSATION_BUSY' });
      }

      let id = request.conversation_id;
      let conv: MockConversation | undefined;
      if (id) {
        conv = conversations.get(id);
        if (!conv) {
          throw new ApiError('api', 'conversation not found', {
            status: 404,
            code: 'CONVERSATION_NOT_FOUND',
            details: { conversation_id: id },
          });
        }
      } else {
        if (!request.wkt) throw new ApiError('api', 'wkt is required', { status: 422, code: 'MISSING_WKT' });
        id = `mock-${Math.random().toString(36).slice(2, 10)}`;
        conv = { area: parseWktPolygon(request.wkt), turns: [], createdAt: new Date().toISOString(), nextItem: 1 };
        conversations.set(id, conv);
      }
      if (conv.turns.length >= 20) {
        throw new ApiError('api', 'limit', { status: 422, code: 'CONVERSATION_LIMIT', details: { max_turns: 20 } });
      }

      const hebrew = /[֐-׿]/.test(request.prompt);
      const count = 3 + (conv.turns.length % 4) + 2;
      const points = randomPoints(conv.area, count, request.prompt.length * 7919 + conv.turns.length + 1);
      const names = hebrew ? NAMES_HE : NAMES_EN;
      const items: ResponseItem[] = points.map((p, i) => ({
        id: `i${conv.nextItem + i}`,
        source_id: 9101,
        name: names[(i + conv.turns.length) % names.length]!,
        lon: p[0]!,
        lat: p[1]!,
        data: {
          address: hebrew ? `רחוב דיזנגוף ${10 + i * 7}, תל אביב-יפו` : `Dizengoff St ${10 + i * 7}, Tel Aviv-Yafo`,
          rating: Math.round((3.8 + ((i * 37) % 12) / 10) * 10) / 10,
          rating_count: 120 + i * 53,
          price_level: PRICES[i % PRICES.length],
          primary_type: 'restaurant',
        },
      }));
      conv.nextItem += items.length;

      const [a, b, c] = items;
      const answer =
        items.length === 0
          ? hebrew
            ? 'לא מצאתי תוצאות מתאימות באזור הזה.'
            : 'I found nothing matching inside this area.'
          : hebrew
            ? `מצאתי ${items.length} מקומות באזור. הבולטים:\n\n- **${a!.name}** [${a!.id}], עם דירוג ${a!.data.rating}.\n${b ? `- ${b.name} [${b.id}] במחיר נוח.\n` : ''}${c ? `- ${c.name} [${c.id}]\n` : ''}\nרוצים שאבדוק שעות פתיחה?`
            : `I found ${items.length} places in the area. Highlights:\n\n- **${a!.name}** [${a!.id}], rated ${a!.data.rating}.\n${b ? `- ${b.name} [${b.id}] is good value.\n` : ''}${c ? `- ${c.name} [${c.id}]\n` : ''}\nWant me to check opening hours?`;

      conv.turns.push({ prompt: request.prompt, answer, items: items.map((i) => i.id), at: new Date().toISOString() });
      const response: AgentResponse = {
        conversation_id: id,
        turn: conv.turns.length,
        request_id: Math.random().toString(16).slice(2),
        area_summary: 'mock · Polygon',
        answer,
        stopped_reason: 'finished',
        usage: {
          model_calls: 3,
          input_tokens: 4200,
          output_tokens: 310,
          peak_input_tokens: 1800,
          over_budget: false,
          summarizer_calls: 1,
          summarizer_input_tokens: 900,
        },
        items,
        answer_source: 'submitted',
      };
      return response;
    },

    async getConversation(conversationId) {
      const conv = conversations.get(conversationId);
      if (!conv) {
        throw new ApiError('api', 'not found', { status: 404, code: 'CONVERSATION_NOT_FOUND' });
      }
      const history: ConversationHistory = {
        conversation_id: conversationId,
        user_id: null,
        area_id: 'mock',
        area_summary: 'mock · Polygon',
        created_at: conv.createdAt,
        updated_at: conv.turns.at(-1)?.at ?? conv.createdAt,
        expires_at: new Date(Date.now() + 7 * 864e5).toISOString(),
        turns: conv.turns.map((t, i) => ({
          turn: i + 1,
          request_id: String(i),
          prompt: t.prompt,
          answer: t.answer,
          item_ids: t.items,
          answer_source: 'submitted',
          stopped_reason: 'finished',
          started_at: t.at,
          finished_at: t.at,
          recovered: false,
        })),
      };
      return history;
    },
  };
}
