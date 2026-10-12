import { useSyncExternalStore } from 'react';

/**
 * Light or dark. With no saved choice the theme follows the OS setting (and
 * keeps following it); once the user picks one, it is saved in this browser.
 * index.html applies the same rule before React loads, so there is no flash.
 */

export type Theme = 'light' | 'dark';
export const THEME_KEY = 'geosearch.theme';

function savedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
}

const media = (): MediaQueryList | null => globalThis.matchMedia?.('(prefers-color-scheme: dark)') ?? null;

export function resolveTheme(): Theme {
  return savedTheme() ?? (media()?.matches ? 'dark' : 'light');
}

const listeners = new Set<() => void>();
let current: Theme = resolveTheme();

function apply(theme: Theme): void {
  current = theme;
  document.documentElement.dataset.theme = theme;
  listeners.forEach((l) => l());
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Not saved, but still applied for this visit.
  }
  apply(theme);
}

export function initTheme(): void {
  apply(resolveTheme());
  media()?.addEventListener('change', () => {
    if (!savedTheme()) apply(resolveTheme());
  });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, () => current);
  return [theme, setTheme];
}
