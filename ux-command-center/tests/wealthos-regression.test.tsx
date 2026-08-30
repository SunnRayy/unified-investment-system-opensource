import React, { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '../test-utils';
import { WealthOS } from '../pages/WealthOS';

const apiMocks = vi.hoisted(() => ({
  getWealthOSAssets: vi.fn(),
  getWealthOSSummary: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
  api: apiMocks,
}));

describe('WealthOS regression', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getWealthOSSummary.mockResolvedValue({
      total_lifetime_gain: 10,
      lifetime_gain_pct: 1,
      annualized_return: 2,
      active_asset_count: 1,
      total_asset_count: 1,
    });
  });

  it('keeps loaded rows when a subsequent refresh returns an empty payload', async () => {
    apiMocks.getWealthOSAssets
      .mockResolvedValueOnce([
        {
          name: 'Test Asset',
          code: 'TEST_1',
          type: 'Equity (股票)',
          period: '1y',
          status: 'ACTIVE',
          invested: 100,
          cur: 120,
          pl: 20,
          ret: 20,
        },
      ])
      .mockResolvedValueOnce([]);

    render(
      <StrictMode>
        <WealthOS />
      </StrictMode>
    );

    await waitFor(() => {
      expect(apiMocks.getWealthOSAssets).toHaveBeenCalledTimes(2);
    });

    expect(await screen.findByText('Test Asset')).toBeInTheDocument();
  });
});
