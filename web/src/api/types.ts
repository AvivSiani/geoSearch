/**
 * Wire types for the GeoSearch API, mirrored from
 * src/geosearch/request/models.py and src/geosearch/errors.py.
 * Keep them in sync when the backend models change.
 */

export type ErrorCode =
  | 'INVALID_REQUEST'
  | 'MISSING_WKT'
  | 'MISSING_PROMPT'
  | 'PROMPT_TOO_LONG'
  | 'WKT_TOO_LARGE'
  | 'WKT_PARSE_ERROR'
  | 'UNSUPPORTED_GEOMETRY'
  | 'INVALID_GEOMETRY'
  | 'OUT_OF_RANGE'
  | 'BUFFER_NOT_APPLICABLE'
  | 'BUFFER_OVERRIDE_NOT_ALLOWED'
  | 'BUFFER_RADIUS_OUT_OF_RANGE'
  | 'AREA_TOO_LARGE'
  | 'MODEL_UNAVAILABLE'
  | 'CONVERSATION_NOT_FOUND'
  | 'AREA_MISMATCH'
  | 'CONVERSATION_BUSY'
  | 'CONVERSATION_LIMIT'
  | 'STORE_UNAVAILABLE';

export interface ErrorEnvelope {
  code: ErrorCode;
  message: string;
  details: Record<string, unknown>;
}

/** POST /v1/requests body. A new conversation sends `wkt`; a follow-up sends
 *  `conversation_id` and no `wkt` (the area is bound to the conversation). */
export interface UserRequest {
  wkt?: string;
  prompt: string;
  conversation_id?: string;
}

export interface UsageSummary {
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  peak_input_tokens: number;
  over_budget: boolean;
  summarizer_calls: number;
  summarizer_input_tokens: number;
}

export interface ResponseItem {
  /** Short conversation id ("i3"); answers cite it as [i3]. */
  id: string;
  source_id: number;
  name: string | null;
  lon: number | null;
  lat: number | null;
  data: Record<string, unknown>;
}

export interface AgentResponse {
  conversation_id: string;
  turn: number;
  request_id: string;
  area_summary: string;
  answer: string;
  stopped_reason: 'finished' | 'call_limit';
  usage: UsageSummary;
  items: ResponseItem[];
  answer_source: 'submitted' | 'fallback';
}

export interface ConversationTurn {
  turn: number;
  request_id: string;
  prompt: string;
  answer: string;
  item_ids: string[];
  answer_source: 'submitted' | 'fallback';
  stopped_reason: 'finished' | 'call_limit';
  started_at: string | null;
  finished_at: string;
  recovered: boolean;
}

export interface ConversationHistory {
  conversation_id: string;
  user_id: null;
  area_id: string;
  area_summary: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  turns: ConversationTurn[];
}
