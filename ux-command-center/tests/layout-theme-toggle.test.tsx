import React from 'react';
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '../test-utils';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { ThemeProvider } from '../src/theme/useTheme';

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

const renderLayout = () =>
  render(
    <ThemeProvider>
      <Layout>
        <Routes>
          <Route path="/" element={<div>home page</div>} />
          <Route path="/risk" element={<div>risk page</div>} />
        </Routes>
      </Layout>
    </ThemeProvider>,
    { initialEntries: ['/'] }
  );

describe('layout theme toggle', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: storageMock,
      writable: true,
    });
    window.localStorage.clear();
    document.documentElement.classList.remove('dark');
  });

  it('toggles day/night and keeps navigation working', async () => {
    const user = userEvent.setup();
    renderLayout();

    const toggle = screen.getByRole('button', { name: /switch to night mode/i });
    await user.click(toggle);

    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(window.localStorage.getItem('uis-theme')).toBe('night');

    await user.click(screen.getByText('Risk Matrix'));
    expect(screen.getByText('risk page')).toBeInTheDocument();
  });
});
