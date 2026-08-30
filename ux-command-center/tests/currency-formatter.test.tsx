/**
 * Tests for the context-aware currency formatter (useFormatCurrency) and
 * the CurrencyContext/CurrencyProvider.
 *
 * Strategy:
 *   - Mock the authFetch so no real network calls are made.
 *   - Render a minimal wrapper that provides CurrencyContext and calls
 *     useFormatCurrency() to produce output.
 *   - Verify CNY passthrough (¥), USD conversion ($), and fallback rate.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { CurrencyProvider, useCurrency } from '../src/context/useCurrency';
import { useFormatCurrency } from '../src/utils/format';

// ── authFetch mock ─────────────────────────────────────────────────────────
// We mock the module so CurrencyProvider's fetch doesn't hit a real server.
const authFetchMock = vi.fn();
vi.mock('../src/services/authFetch', () => ({
  authFetch: (...args: unknown[]) => authFetchMock(...args),
  getAuthToken: () => 'test-token',
  createAuthSSE: vi.fn(),
}));

// ── Helper components ──────────────────────────────────────────────────────

/** Renders a money value and its aria-label for assertion. */
const MoneyDisplay: React.FC<{ value: number; decimals?: number }> = ({ value, decimals = 0 }) => {
  const fmt = useFormatCurrency();
  return <div data-testid="money">{fmt(value, decimals)}</div>;
};

/** Shows the current currency state for assertion. */
const CurrencyState: React.FC = () => {
  const { currency, usdCnyRate, rateIsFallback } = useCurrency();
  return (
    <div>
      <span data-testid="currency">{currency}</span>
      <span data-testid="rate">{usdCnyRate}</span>
      <span data-testid="fallback">{String(rateIsFallback)}</span>
    </div>
  );
};

const renderInProvider = (ui: React.ReactElement) =>
  render(<CurrencyProvider>{ui}</CurrencyProvider>);

// Note: localStorage is already mocked globally by vitest.setup.ts.
// We just need to clear it and set up authFetch before each test.
beforeEach(() => {
  window.localStorage.clear();
  authFetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({ pair: 'USD/CNY', rate: 7.2, as_of: null }),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Tests ──────────────────────────────────────────────────────────────────

describe('CurrencyProvider — defaults', () => {
  it('defaults to CNY and rate 7.0 before fetch resolves', () => {
    // Make fetch never resolve during this synchronous check.
    authFetchMock.mockReturnValue(new Promise(() => {}));
    renderInProvider(<CurrencyState />);
    expect(screen.getByTestId('currency').textContent).toBe('CNY');
    expect(Number(screen.getByTestId('rate').textContent)).toBe(7.0);
    expect(screen.getByTestId('fallback').textContent).toBe('true');
  });

  it('reads persisted currency from localStorage', () => {
    window.localStorage.setItem('uis-reporting-currency', 'USD');
    renderInProvider(<CurrencyState />);
    expect(screen.getByTestId('currency').textContent).toBe('USD');
  });

  it('updates rate from API response', async () => {
    renderInProvider(<CurrencyState />);
    await waitFor(() => expect(screen.getByTestId('rate').textContent).toBe('7.2'));
    expect(screen.getByTestId('fallback').textContent).toBe('false');
  });

  it('keeps fallback rate when fetch fails', async () => {
    authFetchMock.mockRejectedValue(new Error('network error'));
    renderInProvider(<CurrencyState />);
    await waitFor(() => {
      expect(screen.getByTestId('rate').textContent).toBe('7');
      expect(screen.getByTestId('fallback').textContent).toBe('true');
    });
  });

  it('keeps fallback rate when response is not ok', async () => {
    authFetchMock.mockResolvedValue({ ok: false, json: async () => null });
    renderInProvider(<CurrencyState />);
    // Give effect time to run
    await waitFor(() => {
      expect(screen.getByTestId('fallback').textContent).toBe('true');
    });
  });
});

describe('useFormatCurrency — CNY mode', () => {
  it('shows ¥ symbol for CNY values', async () => {
    renderInProvider(<MoneyDisplay value={100000} />);
    // Wait for provider to settle
    await waitFor(() => expect(authFetchMock).toHaveBeenCalled());
    const text = screen.getByTestId('money').textContent;
    expect(text).toMatch(/¥/);
    expect(text).toMatch(/100/); // some form of 100000
  });

  it('passes value through unchanged in CNY mode', () => {
    authFetchMock.mockReturnValue(new Promise(() => {}));
    renderInProvider(<MoneyDisplay value={7200} />);
    const text = screen.getByTestId('money').textContent ?? '';
    expect(text).toMatch(/¥/);
    // In CNY mode the raw number should appear (formatted)
    expect(text).toContain('7,200');
  });
});

describe('useFormatCurrency — USD mode', () => {
  beforeEach(() => {
    // Persist USD selection
    window.localStorage.setItem('uis-reporting-currency', 'USD');
  });

  it('shows $ symbol when USD is selected', async () => {
    renderInProvider(<MoneyDisplay value={72000} />);
    await waitFor(() => expect(authFetchMock).toHaveBeenCalled());
    const text = screen.getByTestId('money').textContent ?? '';
    expect(text).toMatch(/\$/);
  });

  it('divides CNY value by rate to produce USD (rate 7.2, value 7200 → 1000)', async () => {
    // authFetch returns rate 7.2
    renderInProvider(<MoneyDisplay value={7200} decimals={0} />);
    await waitFor(() => {
      const text = screen.getByTestId('money').textContent ?? '';
      // 7200 / 7.2 = 1000
      expect(text).toMatch(/\$1,000|1000/);
    });
  });

  it('uses fallback rate 7.0 when fetch fails, still shows $', async () => {
    authFetchMock.mockRejectedValue(new Error('no network'));
    renderInProvider(<MoneyDisplay value={7000} decimals={0} />);
    await waitFor(() => {
      const text = screen.getByTestId('money').textContent ?? '';
      // 7000 / 7.0 = 1000
      expect(text).toMatch(/\$1,000|1000/);
      expect(text).toMatch(/\$/);
    });
  });
});

describe('FxRateAPI — client shape', () => {
  it('calls /api/market/fx-rate and returns the parsed shape', async () => {
    const { FxRateAPI } = await import('../src/services/api/market');
    authFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ pair: 'USD/CNY', rate: 7.25, as_of: '2026-06-19T00:00:00Z' }),
    });
    const result = await FxRateAPI.get();
    expect(result.pair).toBe('USD/CNY');
    expect(result.rate).toBe(7.25);
    expect(result.as_of).toBe('2026-06-19T00:00:00Z');
    expect(authFetchMock).toHaveBeenCalledWith('/api/market/fx-rate');
  });

  it('throws when response is not ok', async () => {
    const { FxRateAPI } = await import('../src/services/api/market');
    authFetchMock.mockResolvedValue({ ok: false });
    await expect(FxRateAPI.get()).rejects.toThrow('Failed to fetch FX rate');
  });
});
