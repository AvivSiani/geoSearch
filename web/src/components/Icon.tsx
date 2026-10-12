/**
 * Inline stroke icons (DESIGN_SYSTEM §3.4): fill none, round caps and joins.
 * Decorative by default (aria-hidden); the button around one carries the label.
 */

const PATHS = {
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  expand: <path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" />,
  collapse: <path d="M20 10h-6V4M4 14h6v6M14 10l7-7M10 14l-7 7" />,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 13 9 5 9-5" /></>,
  polygon: <path d="M5 8.5 11 4l8 4.5-2 9.5H7.5L5 8.5Z" />,
  rect: <rect x="4.5" y="6" width="15" height="12" rx="1.5" />,
  circle: <circle cx="12" cy="12" r="7.5" />,
  free: <path d="M4 15c2.5-6 5-8 7-6s-1 6 2 7 4.5-4 7-6" />,
  edit: <><path d="M15.5 4.5 19.5 8.5 9 19H5v-4L15.5 4.5Z" /><path d="m13 7 4 4" /></>,
  trash: <><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" /></>,
  send: <path d="M19 12H6m0 0 5-5m-5 5 5 5" />,
  tools: <><path d="M14.5 6.5a3.5 3.5 0 0 0 4.4 4.4l-7.6 7.6a2.1 2.1 0 0 1-3-3l7.6-7.6a3.5 3.5 0 0 0-1.4-1.4Z" /><path d="m17 4-2.5 2.5" /></>,
  agent: <><path d="M12 3.5 13.8 9l5.7 1.5-5.7 1.7L12 18l-1.8-5.8L4.5 10.5 10.2 9 12 3.5Z" /><path d="M18.5 16.5 19 18l1.5.5-1.5.5-.5 1.5-.5-1.5-1.5-.5 1.5-.5.5-1.5Z" /></>,
  user: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 19.5c1.2-3.3 3.8-5 7-5s5.8 1.7 7 5" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  download: <><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5" /><path d="M5 19h14" /></>,
  alert: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5v5.5M12 16.5v.01" /></>,
  retry: <><path d="M5 12a7 7 0 1 0 2.1-5" /><path d="M5 4v4h4" /></>,
  pin: <><path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11Z" /><circle cx="12" cy="10" r="2.3" /></>,
  globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5Z" /></>,
  phone: <path d="M6.5 4h3l1.5 4-2 1.5a10 10 0 0 0 5.5 5.5l1.5-2 4 1.5v3a2 2 0 0 1-2 2A15 15 0 0 1 4.5 6a2 2 0 0 1 2-2Z" />,
  moon: <path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10Z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.01" /></>,
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 16,
  strokeWidth = 2,
  className,
}: {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
