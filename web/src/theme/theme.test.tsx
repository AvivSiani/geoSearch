import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { TopBar } from '../components/TopBar';
import { he } from '../i18n/he';
import { initTheme, THEME_KEY } from './theme';

describe('theme toggle', () => {
  it('switches the page theme and remembers the choice', async () => {
    act(() => initTheme());
    render(<TopBar />);
    const root = document.documentElement;
    expect(root.dataset.theme).toBe('light'); // jsdom has no dark OS preference

    await userEvent.click(screen.getByRole('button', { name: he.theme.toDark }));
    expect(root.dataset.theme).toBe('dark');
    expect(localStorage.getItem(THEME_KEY)).toBe('dark');

    await userEvent.click(screen.getByRole('button', { name: he.theme.toLight }));
    expect(root.dataset.theme).toBe('light');
    expect(localStorage.getItem(THEME_KEY)).toBe('light');
  });
});
