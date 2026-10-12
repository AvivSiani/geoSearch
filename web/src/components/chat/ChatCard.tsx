import { AGENT_TOOLS } from '../../data/agentTools';
import { he } from '../../i18n/he';
import { useAppState, useDispatch } from '../../state/AppStateProvider';
import { currentConversation, isWorking } from '../../state/reducer';
import { Composer } from './Composer';
import { Icon } from '../Icon';
import { MessageList } from './MessageList';
import './ChatCard.css';

export function ChatCard({ hidden }: { hidden?: boolean }) {
  const state = useAppState();
  const dispatch = useDispatch();
  const conv = currentConversation(state);
  const working = isWorking(conv);

  return (
    <section className="chat-card" aria-label={he.chat.aria} hidden={hidden}>
      <header className="chat-header">
        <div className="chat-header__who">
          <span className="chat-header__avatar" aria-hidden="true">
            <Icon name="agent" size={18} />
          </span>
          <div>
            <h2 className="chat-header__title">{he.chat.title}</h2>
            <p className="chat-header__status" aria-live="polite">
              <span className={`status-dot${working ? ' status-dot--working' : ''}`} aria-hidden="true" />
              {working ? he.chat.statusWorking : he.chat.status(AGENT_TOOLS.length)}
            </p>
          </div>
        </div>
        <div className="chat-header__actions">
          <button
            type="button"
            className={`header-btn${state.toolsOpen ? ' is-open' : ''}`}
            aria-expanded={state.toolsOpen}
            aria-controls="agent-tools"
            onClick={() => dispatch({ type: 'SET_TOOLS_OPEN', open: !state.toolsOpen })}
          >
            <Icon name="tools" size={16} />
            {he.chat.toolsToggle(AGENT_TOOLS.length)}
          </button>
          <button
            type="button"
            className="header-btn"
            onClick={() => dispatch({ type: 'NEW_CHAT' })}
            disabled={!conv && !state.draftNotice}
          >
            <Icon name="plus" size={16} />
            {he.chat.new}
          </button>
        </div>
      </header>
      <MessageList />
      <Composer />
    </section>
  );
}
