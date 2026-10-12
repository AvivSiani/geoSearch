import { useId } from 'react';

/** DESIGN_SYSTEM §5.2: a magnifying glass whose lens shows a tiny light map. */
export function Logo({ size = 40 }: { size?: number }) {
  const uid = useId().replace(/:/g, '');
  const grad = `logo-grad-${uid}`;
  const clip = `logo-clip-${uid}`;
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={grad} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5CC6F2" />
          <stop offset="1" stopColor="#2B84C9" />
        </linearGradient>
        <clipPath id={clip}>
          <circle cx="17" cy="17" r="11" />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clip})`}>
        <rect x="6" y="6" width="22" height="22" fill="#EEF5FB" />
        <path d="M6 22c4-1 6-5 10-4s6 4 12 2v8H6Z" fill="#CDEBC9" />
        <path d="M6 12c5 1 8 4 11 9s6 6 11 6" fill="none" stroke="#8FCDF4" strokeWidth="3.2" />
        <path d="M6 16h22M6 23h22M13 6v22M22 6v22" stroke="#FFFFFF" strokeWidth="1.6" />
      </g>
      <circle cx="17" cy="17" r="12" fill="none" stroke={`url(#${grad})`} strokeWidth="3.4" />
      <path d="M26 26 34.5 34.5" stroke={`url(#${grad})`} strokeWidth="5" strokeLinecap="round" />
      <path
        d="M17 21.5s-3.6-3.3-3.6-6a3.6 3.6 0 0 1 7.2 0c0 2.7-3.6 6-3.6 6Z"
        fill="#7D8BFA"
        stroke="#FFFFFF"
        strokeWidth="1.2"
      />
      <circle cx="17" cy="15.4" r="1.2" fill="#FFFFFF" />
    </svg>
  );
}
