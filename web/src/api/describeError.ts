import { he } from '../i18n/he';
import { ApiError } from './client';

/**
 * What the chat shows for a failed turn. `retryable` offers a retry button
 * with the same prompt; `action` tells the store how the conversation changes.
 */
export interface ErrorView {
  text: string;
  code: string;
  retryable: boolean;
  action: 'none' | 'expired' | 'limit';
}

export function describeError(err: unknown): ErrorView {
  if (!(err instanceof ApiError)) {
    return { text: he.errors.unknown, code: 'UNKNOWN', retryable: true, action: 'none' };
  }
  switch (err.kind) {
    case 'network':
      return { text: he.errors.network, code: 'NETWORK', retryable: true, action: 'none' };
    case 'timeout':
      return { text: he.errors.timeout, code: 'TIMEOUT', retryable: true, action: 'none' };
    case 'bad_response':
      return { text: he.errors.badResponse, code: 'BAD_RESPONSE', retryable: true, action: 'none' };
    case 'aborted':
      return { text: he.chat.interrupted, code: 'ABORTED', retryable: true, action: 'none' };
    case 'api':
      break;
  }

  const code = err.code ?? 'UNKNOWN';
  switch (code) {
    case 'MODEL_UNAVAILABLE':
    case 'STORE_UNAVAILABLE':
    case 'CONVERSATION_BUSY':
      return { text: he.errors[code], code, retryable: true, action: 'none' };
    case 'CONVERSATION_NOT_FOUND':
      // The next send starts a fresh server conversation on the same area.
      return {
        text: `${he.errors.CONVERSATION_NOT_FOUND} ${he.chat.expiredContinue}`,
        code,
        retryable: true,
        action: 'expired',
      };
    case 'CONVERSATION_LIMIT':
      return {
        text: he.errors.CONVERSATION_LIMIT(err.details.max_turns),
        code,
        retryable: false,
        action: 'limit',
      };
    case 'AREA_MISMATCH':
    case 'AREA_TOO_LARGE':
    case 'INVALID_GEOMETRY':
    case 'WKT_TOO_LARGE':
    case 'WKT_PARSE_ERROR':
    case 'UNSUPPORTED_GEOMETRY':
    case 'OUT_OF_RANGE':
    case 'PROMPT_TOO_LONG':
    case 'MISSING_PROMPT':
    case 'MISSING_WKT':
      return { text: he.errors[code], code, retryable: false, action: 'none' };
    default:
      return { text: he.errors.generic, code, retryable: false, action: 'none' };
  }
}
