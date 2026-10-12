import { useEffect, useId, useRef, useState } from 'react';
import { AGENT_TOOLS, GROUP_ORDER } from '../../data/agentTools';
import { he } from '../../i18n/he';
import { useDispatch } from '../../state/AppStateProvider';
import { Icon } from '../Icon';
import { ToolRow } from './ToolRow';
import './AgentToolsPanel.css';

/**
 * Read-only list of the agent's tools, opened from the chat header. No
 * switches, search or "add tool" (removed on purpose in the design).
 */
export function AgentToolsPanel() {
  const dispatch = useDispatch();
  const [openId, setOpenId] = useState<string | null>(null);
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  // Opening moves focus into the panel; closing returns it to the toggle.
  useEffect(() => {
    closeRef.current?.focus();
  }, []);
  const close = () => {
    dispatch({ type: 'SET_TOOLS_OPEN', open: false });
    requestAnimationFrame(() =>
      document.querySelector<HTMLButtonElement>('[aria-controls="agent-tools"]')?.focus(),
    );
  };

  return (
    <aside
      id="agent-tools"
      className="tools-panel"
      aria-labelledby={titleId}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          close();
        }
      }}
    >
      <div className="tools-panel__header">
        <div className="tools-panel__title-row">
          <Icon name="tools" size={18} className="tools-panel__icon" />
          <h2 id={titleId} className="tools-panel__title">
            {he.tools.title}
          </h2>
          <button
            ref={closeRef}
            type="button"
            className="icon-close"
            aria-label={he.tools.close}
            title={he.tools.close}
            onClick={close}
          >
            <Icon name="close" size={16} />
          </button>
        </div>
        <p className="tools-panel__explainer">{he.tools.explainer}</p>
      </div>
      <div className="tools-panel__list">
        {GROUP_ORDER.map((group) => {
          const tools = AGENT_TOOLS.filter((t) => t.group === group);
          if (tools.length === 0) return null;
          return (
            <section key={group} className="tools-panel__group" aria-label={he.tools.group[group]}>
              <h3 className="tools-panel__group-label">{he.tools.group[group]}</h3>
              <ul className="tools-panel__rows">
                {tools.map((tool) => (
                  <ToolRow
                    key={tool.id}
                    tool={tool}
                    open={openId === tool.id}
                    onOpen={() => setOpenId(tool.id)}
                    onClose={() => setOpenId((id) => (id === tool.id ? null : id))}
                  />
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </aside>
  );
}
