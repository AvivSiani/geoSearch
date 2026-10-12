import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { he } from '../../i18n/he';
import { blockReason, useActions, useAppState, useDispatch } from '../../state/AppStateProvider';
import { currentConversation, isWorking } from '../../state/reducer';
import { Icon } from '../Icon';

/**
 * The question box. Enter sends, Shift+Enter adds a line (and Enter while an
 * IME is composing does nothing). The area chip shows what the question is
 * scoped to; removing it clears the area.
 */
export function Composer() {
  const state = useAppState();
  const actions = useActions();
  const dispatch = useDispatch();
  const [text, setText] = useState('');
  const [feedback, setFeedback] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const inputId = useId();
  const feedbackId = useId();

  const conv = currentConversation(state);
  const area = conv?.area ?? state.area;
  // Problems the user should see before trying (area or conversation state).
  // An empty box is not a problem worth announcing.
  const blocked = blockReason(state, text.trim() === '' ? '·' : text);
  const canSend = blocked === null && text.trim() !== '';

  // Switching conversations clears stale feedback.
  useEffect(() => setFeedback(null), [state.currentId]);

  const send = () => {
    if (text.trim() === '') return;
    const result = actions.ask(text);
    if (result.ok) {
      setText('');
      setFeedback(null);
      inputRef.current?.focus();
    } else {
      setFeedback(result.reason);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  // "Draw an area" is already the placeholder, and "working" is in the header,
  // so those two are only said after a send attempt.
  const proactive = blocked !== null && blocked !== he.chat.needArea && !isWorking(conv) ? blocked : null;
  const message = proactive ?? feedback;

  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <label htmlFor={inputId} className="sr-only">
          {he.chat.inputLabel}
        </label>
        <textarea
          id={inputId}
          ref={inputRef}
          className="composer__input"
          rows={2}
          dir="auto"
          value={text}
          placeholder={area ? he.chat.placeholder : he.chat.placeholderNoArea}
          onChange={(e) => {
            setText(e.target.value);
            setFeedback(null);
          }}
          onKeyDown={onKeyDown}
          aria-describedby={message ? feedbackId : undefined}
        />
        <div className="composer__bar">
          <div className="composer__chips">
            {area && (
              <span className="area-chip">
                <Icon name="polygon" size={13} />
                <span>{he.chat.areaChip(area.seq, area.km2)}</span>
                <button
                  type="button"
                  className="area-chip__remove"
                  aria-label={he.chat.areaChipRemove}
                  title={he.chat.areaChipRemove}
                  onClick={() => dispatch({ type: 'AREA_CLEARED' })}
                >
                  <Icon name="close" size={12} strokeWidth={2.4} />
                </button>
              </span>
            )}
          </div>
          <span className="composer__hint" aria-hidden="true">
            {he.chat.sendHint}
          </span>
          <button type="submit" className="send-btn" aria-label={he.chat.sendAria} disabled={!canSend}>
            <Icon name="send" size={18} strokeWidth={2.2} />
          </button>
        </div>
      </form>
      {message && (
        <p id={feedbackId} className="composer__feedback" role="status">
          {message}
        </p>
      )}
    </div>
  );
}
