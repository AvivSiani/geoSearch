import { he } from '../../i18n/he';
import { Icon } from '../Icon';

export function MapControls({
  expanded,
  onToggleExpand,
  basemapLabel,
  canCycleBasemap,
  onCycleBasemap,
  onZoomIn,
  onZoomOut,
}: {
  expanded: boolean;
  onToggleExpand(): void;
  basemapLabel: string;
  canCycleBasemap: boolean;
  onCycleBasemap(): void;
  onZoomIn(): void;
  onZoomOut(): void;
}) {
  return (
    <div className="map-controls">
      <button
        type="button"
        className="map-btn"
        aria-label={expanded ? he.map.collapse : he.map.expand}
        aria-pressed={expanded}
        title={expanded ? he.map.collapse : he.map.expand}
        onClick={onToggleExpand}
      >
        <Icon name={expanded ? 'collapse' : 'expand'} size={16} />
      </button>
      {canCycleBasemap && (
        <button
          type="button"
          className="map-btn"
          aria-label={he.map.basemapCurrent(basemapLabel)}
          title={he.map.basemapCurrent(basemapLabel)}
          onClick={onCycleBasemap}
        >
          <Icon name="layers" size={16} />
        </button>
      )}
      <div className="map-zoom" role="group">
        <button type="button" className="map-zoom__btn" aria-label={he.map.zoomOut} title={he.map.zoomOut} onClick={onZoomOut}>
          <Icon name="minus" size={16} />
        </button>
        <span className="map-zoom__divider" aria-hidden="true" />
        <button type="button" className="map-zoom__btn" aria-label={he.map.zoomIn} title={he.map.zoomIn} onClick={onZoomIn}>
          <Icon name="plus" size={16} />
        </button>
      </div>
    </div>
  );
}
