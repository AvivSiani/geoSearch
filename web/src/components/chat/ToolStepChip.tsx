import { Icon } from '../Icon';

/** "✓ tool name · detail"; while running, a pulsing dot replaces the check. */
export function ToolStepChip({ name, detail, running }: { name: string; detail: string; running?: boolean }) {
  return (
    <span className={`step-chip${running ? ' step-chip--running' : ''}`}>
      {running ? <span className="step-chip__pulse" aria-hidden="true" /> : <Icon name="check" size={13} strokeWidth={2.4} className="step-chip__check" />}
      <span className="step-chip__name">{name}</span>
      <span aria-hidden="true">·</span>
      <span>{detail}</span>
    </span>
  );
}
