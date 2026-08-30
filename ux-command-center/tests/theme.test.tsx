import React from 'react';
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider, useTheme } from '../src/theme/useTheme';

const ThemeProbe = () => {
  const { mode, setMode, toggleMode } = useTheme();

  return (
    <div>
      <span data-testid="theme-mode">{mode}</span>
      <button onClick={() => setMode('night')}>set-night</button>
      <button onClick={() => toggleMode()}>toggle</button>
    </div>
  );
};

const storageData = new Map<string, string>();

const storageMock: Storage = {
  get length() {
    return storageData.size;
  },
  clear: () => {
    storageData.clear();
  },
  getItem: (key: string) => (storageData.has(key) ? storageData.get(key)! : null),
  key: (index: number) => Array.from(storageData.keys())[index] ?? null,
  removeItem: (key: string) => {
    storageData.delete(key);
  },
  setItem: (key: string, value: string) => {
    storageData.set(key, value);
  },
};

describe('theme provider', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: storageMock,
      writable: true,
    });
    window.localStorage.removeItem('uis-theme');
    document.documentElement.classList.remove('dark');
  });

  it('defaults to day when no preference exists', () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme-mode')).toHaveTextContent('day');
    expect(window.localStorage.getItem('uis-theme')).toBe('day');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('reads persisted night preference', () => {
    window.localStorage.setItem('uis-theme', 'night');

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme-mode')).toHaveTextContent('night');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('persists value and updates root class when toggled', async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>
    );

    await user.click(screen.getByRole('button', { name: 'set-night' }));
    expect(screen.getByTestId('theme-mode')).toHaveTextContent('night');
    expect(window.localStorage.getItem('uis-theme')).toBe('night');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    await user.click(screen.getByRole('button', { name: 'toggle' }));
    expect(screen.getByTestId('theme-mode')).toHaveTextContent('day');
    expect(window.localStorage.getItem('uis-theme')).toBe('day');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });
});
