import { useId } from 'react';
import type { AgentTool } from '../../data/agentTools';
import { Icon } from '../Icon';

/**
 * A focusable row whose description shows on hover or focus only. The text
 * stays in the DOM (visually hidden when closed) so aria-describedby always
 * reads it.
 */
export function ToolRow({
  tool,
  open,
  onOpen,
  onClose,
}: {
  tool: AgentTool;
  open: boolean;
  onOpen(): void;
  onClose(): void;
}) {
  const descId = useId();
  return (
    <li
      className={`tool-row${open ? ' is-open' : ''}`}
      tabIndex={0}
      aria-describedby={descId}
      onMouseEnter={onOpen}
      onMouseLeave={onClose}
      onFocus={onOpen}
      onBlur={onClose}
    >
      <span className="tool-row__head">
        <span className="tool-row__tile" aria-hidden="true">
          <Icon name={tool.icon} size={18} />
        </span>
        <span className="tool-row__name">{tool.name}</span>
      </span>
      <span id={descId} className={open ? 'tool-row__desc' : 'sr-only'}>
        {tool.description}
      </span>
    </li>
  );
}
