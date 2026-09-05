import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { MarketDataStatus } from '../components/settings/MarketDataStatus';

const settingsMocks = vi.hoisted(() => ({
  getMarketDataStatus: vi.fn(),
  refreshMarketData: vi.fn(),
}));

vi.mock('../src/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/services/api')>();
  return {
    ...actual,
    SettingsAPI: {
      ...actual.SettingsAPI,
      getMarketDataStatus: settingsMocks.getMarketDataStatus,
      refreshMarketData: settingsMocks.refreshMarketData,
    },
  };
});

describe('MarketDataStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders providers, staleness badge, and refresh summary', async () => {
    settingsMocks.getMarketDataStatus.mockResolvedValue({
      last_refresh: {
        refreshed: 8,
        skipped: 15,
        errors: 0,
        holdings_updated: 41,
        fx_rates: { USD: 7.1234, HKD: 0.9123 },
        refreshed_assets: [
          {
            asset_id: 'US_STK_AMZN',
            code: 'AMZN',
            market: 'us',
            price: 201.25,
            as_of_date: '2026-03-27',
            source: 'yfinance',
          },
        ],
        skipped_assets: [],
        error_assets: [],
        timestamp: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
      },
      providers: [
        { market: 'us', fetcher: 'yfinance', asset_count: 8, status: 'active' },
        { market: 'cn_fund', fetcher: 'akshare', asset_count: 15, status: 'active' },
      ],
      staleness: 'fresh',
    });

    render(<MarketDataStatus />);

    expect(await screen.findByText('Market Data Providers')).toBeInTheDocument();
    expect(screen.getByText('Fresh')).toBeInTheDocument();
    expect(screen.getByText('8 refreshed, 15 skipped, 0 errors')).toBeInTheDocument();
    expect(screen.getByText('US Market')).toBeInTheDocument();
    expect(screen.getByText('CN Funds')).toBeInTheDocument();
    expect(screen.getByText('yfinance')).toBeInTheDocument();
    expect(screen.getByText('akshare')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /show refresh details/i })).toBeInTheDocument();
  });

  it('shows fetched prices, fx rates, and refresh errors in details panel', async () => {
    settingsMocks.getMarketDataStatus.mockResolvedValue({
      last_refresh: {
        refreshed: 2,
        skipped: 0,
        errors: 1,
        holdings_updated: 6,
        fx_rates: { USD: 7.1234, HKD: 0.9123 },
        refreshed_assets: [
          {
            asset_id: 'US_STK_AMZN',
            code: 'AMZN',
            market: 'us',
            price: 201.25,
            as_of_date: '2026-03-27',
            source: 'yfinance',
          },
          {
            asset_id: 'CN_FUND_900008',
            code: '900008',
            market: 'cn_fund',
            price: 1.234,
            as_of_date: '2026-03-27',
            source: 'akshare_fund',
          },
        ],
        skipped_assets: [],
        error_assets: [
          {
            asset_id: 'US_ETF_VOO',
            market: 'us',
            reason: 'quote unavailable',
          },
        ],
        timestamp: new Date().toISOString(),
      },
      providers: [
        { market: 'us', fetcher: 'yfinance', asset_count: 8, status: 'active' },
        { market: 'cn_fund', fetcher: 'akshare', asset_count: 19, status: 'active' },
      ],
      staleness: 'fresh',
    });

    const user = userEvent.setup();
    render(<MarketDataStatus />);

    expect(await screen.findByText('2 refreshed, 0 skipped, 1 errors')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show refresh details/i }));

    expect(screen.getByText('Latest fetched prices')).toBeInTheDocument();
    expect(screen.getByText('AMZN')).toBeInTheDocument();
    expect(screen.getByText('900008')).toBeInTheDocument();
    expect(screen.getByText('201.25')).toBeInTheDocument();
    expect(screen.getByText('1.234')).toBeInTheDocument();
    expect(screen.getByText('Refresh errors')).toBeInTheDocument();
    expect(screen.getByText('US_ETF_VOO')).toBeInTheDocument();
    expect(screen.getByText('quote unavailable')).toBeInTheDocument();
    expect(screen.getByText('USD/CNY 7.1234')).toBeInTheDocument();
    expect(screen.getByText('HKD/CNY 0.9123')).toBeInTheDocument();
  });

  it('refreshes prices and reloads the status card', async () => {
    settingsMocks.getMarketDataStatus
      .mockResolvedValueOnce({
        last_refresh: null,
        providers: [{ market: 'us', fetcher: 'yfinance', asset_count: 2, status: 'active' }],
        staleness: 'never',
      })
      .mockResolvedValueOnce({
        last_refresh: {
          refreshed: 3,
          skipped: 1,
          errors: 0,
          holdings_updated: 6,
          fx_rates: { USD: 7.1111, HKD: 0.9111 },
          refreshed_assets: [],
          skipped_assets: [],
          error_assets: [],
          timestamp: new Date().toISOString(),
        },
        providers: [{ market: 'us', fetcher: 'yfinance', asset_count: 2, status: 'active' }],
        staleness: 'fresh',
      });
    settingsMocks.refreshMarketData.mockResolvedValue({
      refreshed: 3,
      skipped: 1,
      errors: 0,
      holdings_updated: 6,
      fx_rates: { USD: 7.1111, HKD: 0.9111 },
      refreshed_assets: [],
      skipped_assets: [],
      error_assets: [],
      timestamp: new Date().toISOString(),
    });

    const user = userEvent.setup();
    render(<MarketDataStatus />);

    expect(await screen.findByText('Never Synced')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /refresh prices/i }));

    await waitFor(() => {
      expect(settingsMocks.refreshMarketData).toHaveBeenCalledTimes(1);
      expect(screen.getByText('3 refreshed, 1 skipped, 0 errors')).toBeInTheDocument();
    });
  });
});
