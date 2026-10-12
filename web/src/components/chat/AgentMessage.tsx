import { Fragment, useMemo } from 'react';
import type { ResponseItem } from '../../api/types';
import { parseAnswer, type Inline } from '../../chat/answerText';
import { AGENT_TOOLS } from '../../data/agentTools';
import { he } from '../../i18n/he';
import { useActions, useAppState, useDispatch } from '../../state/AppStateProvider';
import { resultsMessage } from '../../state/reducer';
import type { AgentMessage } from '../../state/types';
import { Icon } from '../Icon';
import { ResultCard } from './ResultCard';
import { ToolStepChip } from './ToolStepChip';

export function AgentAvatar() {
  return (
    <span className="agent-msg__avatar" aria-hidden="true">
      <Icon name="agent" size={16} />
    </span>
  );
}

/** Steps we can state as fact: which tools produced this answer's items. */
function stepsFor(items: ResponseItem[]): { name: string; detail: string }[] {
  const bySource = new Map<number, number>();
  for (const item of items) bySource.set(item.source_id, (bySource.get(item.source_id) ?? 0) + 1);
  return [...bySource].map(([sourceId, count]) => ({
    name: AGENT_TOOLS.find((t) => t.id === String(sourceId))?.name ?? `source_${sourceId}`,
    detail: he.history.results(count),
  }));
}

export function AgentMessageView({ conversationId, message }: { conversationId: string; message: AgentMessage }) {
  const actions = useActions();
  const dispatch = useDispatch();
  const state = useAppState();
  const items = useMemo(() => message.items ?? [], [message.items]);
  const blocks = useMemo(() => (message.answer ? parseAnswer(message.answer) : []), [message.answer]);
  /** Citation [i3] → its number in this answer's result list. */
  const numberOf = useMemo(() => new Map(items.map((item, i) => [item.id, i + 1])), [items]);
  const onMap = resultsMessage(state)?.id === message.id;

  const activate = (itemId: string) => {
    if (!onMap) dispatch({ type: 'FOCUS_MESSAGE', messageId: message.id });
    dispatch({ type: 'SET_ACTIVE_ITEM', item: { messageId: message.id, itemId } });
  };

  const renderInline = (parts: Inline[]) =>
    parts.map((part, i) => {
      if (part.t === 'text') return part.bold ? <strong key={i}>{part.v}</strong> : <Fragment key={i}>{part.v}</Fragment>;
      const cited = part.ids.filter((id) => numberOf.has(id));
      if (cited.length === 0) return null;
      return (
        <Fragment key={i}>
          {' '}
          {cited.map((id) => {
            const n = numberOf.get(id)!;
            return (
              <button
                key={id}
                type="button"
                className="cite"
                aria-label={he.results.citeAria(n)}
                onClick={() => activate(id)}
                onMouseEnter={() => onMap && dispatch({ type: 'SET_ACTIVE_ITEM', item: { messageId: message.id, itemId: id } })}
                onMouseLeave={() => onMap && dispatch({ type: 'SET_ACTIVE_ITEM', item: null })}
              >
                {n}
              </button>
            );
          })}
        </Fragment>
      );
    });

  return (
    <div className="agent-msg" aria-busy={message.status === 'working'}>
      <AgentAvatar />
      <div className="agent-msg__body">
        {message.status === 'working' && (
          <>
            <div className="steps">
              <ToolStepChip name={he.chat.title} detail={he.chat.workingStep} running />
            </div>
            <p className="agent-msg__text agent-msg__text--working">{he.chat.working}</p>
          </>
        )}

        {message.status === 'error' && message.error && (
          <div className="agent-error" role="alert">
            <Icon name="alert" size={16} />
            <div className="agent-error__body">
              <p className="agent-error__text">{message.error.text}</p>
              {message.error.retryable && (
                <button type="button" className="pill-btn" onClick={() => actions.retry(conversationId, message.id)}>
                  <Icon name="retry" size={14} />
                  {he.chat.retry}
                </button>
              )}
              {message.error.action === 'limit' && (
                <button type="button" className="pill-btn" onClick={() => dispatch({ type: 'NEW_CHAT' })}>
                  <Icon name="plus" size={14} />
                  {he.chat.new}
                </button>
              )}
            </div>
          </div>
        )}

        {message.status === 'done' && (
          <>
            {items.length > 0 && (
              <div className="steps">
                {stepsFor(items).map((s) => (
                  <ToolStepChip key={s.name} name={s.name} detail={s.detail} />
                ))}
              </div>
            )}
            <div className="agent-msg__text" dir="auto">
              {blocks.map((block, i) => {
                switch (block.type) {
                  case 'heading':
                    return (
                      <p key={i} className="agent-msg__heading" dir="auto">
                        {renderInline(block.content)}
                      </p>
                    );
                  case 'p':
                    return (
                      <p key={i} dir="auto">
                        {block.lines.map((line, j) => (
                          <Fragment key={j}>
                            {j > 0 && <br />}
                            {renderInline(line)}
                          </Fragment>
                        ))}
                      </p>
                    );
                  case 'ul':
                  case 'ol': {
                    const List = block.type;
                    return (
                      <List key={i} dir="auto">
                        {block.items.map((item, j) => (
                          <li key={j}>{renderInline(item)}</li>
                        ))}
                      </List>
                    );
                  }
                }
              })}
            </div>
            {message.stoppedReason === 'call_limit' && <p className="agent-msg__warn">{he.chat.callLimit}</p>}
            {items.length > 0 && (
              <ResultCard
                messageId={message.id}
                items={items}
                onMap={onMap}
                onShowOnMap={() => dispatch({ type: 'FOCUS_MESSAGE', messageId: message.id })}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
