import { useEffect, useRef, useState } from 'react';
import { formatLatLon } from '../../geo/coords';
import type { LatLon } from '../../geo/types';
import { he } from '../../i18n/he';

export type CoordsSink = (position: LatLon) => void;

/**
 * Live cursor position (or the map center). Updates arrive many times a
 * second, so they are batched per animation frame and kept in this component's
 * own state instead of re-rendering the map card.
 */
export function CoordinateReadout({
  initial,
  register,
  attribution,
}: {
  initial: LatLon;
  register(sink: CoordsSink | null): void;
  /** Basemap attribution (trusted deployment config, may contain links). */
  attribution: string;
}) {
  const [pos, setPos] = useState(initial);
  const pending = useRef<LatLon | null>(null);
  const frame = useRef(0);

  useEffect(() => {
    register((p) => {
      pending.current = p;
      if (frame.current) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = 0;
        if (pending.current) setPos(pending.current);
      });
    });
    return () => {
      register(null);
      cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [register]);

  return (
    <div className="coords">
      <span className="sr-only">{he.map.coordsAria}: </span>
      <span className="coords__value mono" dir="ltr">
        {formatLatLon(pos)}
      </span>
      {attribution && (
        <span className="coords__attribution" dir="ltr" dangerouslySetInnerHTML={{ __html: attribution }} />
      )}
    </div>
  );
}
