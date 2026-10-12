import type { DrawMode } from '../../map/GeoMap';
import { he } from '../../i18n/he';
import { Icon, type IconName } from '../Icon';

const MODES: { mode: DrawMode; label: string; icon: IconName }[] = [
  { mode: 'polygon', label: he.draw.polygon, icon: 'polygon' },
  { mode: 'rect', label: he.draw.rect, icon: 'rect' },
  { mode: 'circle', label: he.draw.circle, icon: 'circle' },
  { mode: 'free', label: he.draw.free, icon: 'free' },
  { mode: 'edit', label: he.draw.edit, icon: 'edit' },
];

/**
 * One mode is selected at a time (aria-pressed). Pressing it arms the map for
 * one shape; pressing it again while armed (or Escape) stops. Switching modes
 * never deletes the area: only a finished new shape replaces it.
 */
export function DrawToolbar({
  mode,
  armed,
  hasArea,
  onSelect,
  onClear,
}: {
  mode: DrawMode;
  armed: boolean;
  hasArea: boolean;
  onSelect(mode: DrawMode): void;
  onClear(): void;
}) {
  return (
    <div className="draw-toolbar" role="toolbar" aria-label={he.draw.toolbarAria}>
      {MODES.map((m) => (
        <button
          key={m.mode}
          type="button"
          className={`draw-toolbar__btn${mode === m.mode ? ' is-active' : ''}${mode === m.mode && armed ? ' is-armed' : ''}`}
          aria-pressed={mode === m.mode}
          disabled={m.mode === 'edit' && !hasArea}
          onClick={() => onSelect(m.mode)}
          title={m.label}
        >
          <Icon name={m.icon} size={16} />
          <span className="draw-toolbar__label">{m.label}</span>
        </button>
      ))}
      <span className="draw-toolbar__divider" aria-hidden="true" />
      <button
        type="button"
        className="draw-toolbar__btn draw-toolbar__btn--danger"
        onClick={onClear}
        disabled={!hasArea}
        title={he.draw.clear}
      >
        <Icon name="trash" size={16} />
        <span className="draw-toolbar__label">{he.draw.clear}</span>
      </button>
    </div>
  );
}
