import { useEffect, useLayoutEffect, useRef } from 'react';
import { he } from '../../i18n/he';
import { useActions, useAppState } from '../../state/AppStateProvider';
import { currentConversation } from '../../state/reducer';
import { AgentAvatar, AgentMessageView } from './AgentMessage';
import { SuggestionChips } from './SuggestionChips';

/**
 * The conversation on screen, or the greeting for a new chat. Sticks to the
 * bottom as messages arrive unless the reader scrolled up.
 */
export function MessageList() {
  const state = useAppState();
  const actions = useActions();
  const conv = currentConversation(state);
  const messages = conv?.messages ?? [];
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // A conversation switch always starts at the bottom.
  useLayoutEffect(() => {
    stick.current = true;
  }, [conv?.id]);

  const last = messages[messages.length - 1];
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, last, conv?.id, state.draftNotice]);

  return (
    <div className="messages" ref={scroller}>
      <div className="messages__column" role="log" aria-live="polite" aria-relevant="additions">
        <div className="agent-msg">
          <AgentAvatar />
          <div className="agent-msg__body">
            <p className="agent-msg__text">{he.chat.greeting}</p>
            {!conv && <SuggestionChips onPick={(text) => actions.ask(text)} disabled={!state.area} />}
          </div>
        </div>

        {!conv && state.draftNotice && <p className="notice">{state.draftNotice}</p>}

        {messages.map((m) => {
          if (m.role === 'user') {
            return (
              <div key={m.id} className="user-msg">
                <span className="sr-only">{he.chat.youSaid}: </span>
                <p className="user-msg__bubble" dir="auto">
                  {m.text}
                </p>
              </div>
            );
          }
          if (m.role === 'notice') {
            return (
              <p key={m.id} className="notice">
                {m.text}
              </p>
            );
          }
          return <AgentMessageView key={m.id} conversationId={conv!.id} message={m} />;
        })}
      </div>
    </div>
  );
}
