import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SourceHealthDashboard } from '../components/settings/SourceHealthDashboard';

const settingsMocks = vi.hoisted(() => ({
  getSourceHealth: vi.fn(),
}));

vi.mock('../src/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/services/api')>();
  return {
    ...actual,
    SettingsAPI: {
      ...actual.SettingsAPI,
      getSourceHealth: settingsMocks.getSourceHealth,
    },
  };
});

describe('SourceHealthDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders source cards with null-safe fallbacks and stale warning', async () => {
    settingsMocks.getSourceHealth.mockResolvedValue({
      last_sync_at: null,
      all_healthy: false,
      sources: [
        {
          reader: 'schwab',
          last_sync_at: null,
          row_count: null,
          net_value_cny: null,
          file_path: null,
          file_modified: null,
          file_size_bytes: null,
          file_stale: true,
          status: 'missing',
        },
        {
          reader: 'cn_fund',
          last_sync_at: new Date().toISOString(),
          row_count: 15,
          net_value_cny: 1234567,
          file_path: '/tmp/funds.xlsx',
          file_modified: new Date().toISOString(),
          file_size_bytes: 1024,
          file_stale: false,
          status: 'ok',
        },
      ],
    });

    render(<SourceHealthDashboard labelLookup={{ schwab: 'Schwab Brokerage', cn_fund: 'Chinese Mutual Funds' }} />);

    expect(await screen.findByText('Source Health')).toBeInTheDocument();
    expect(await screen.findByText('Schwab Brokerage')).toBeInTheDocument();
    expect(screen.getByText('Never synced')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('File stale')).toBeInTheDocument();
    expect(screen.getByText('¥1,234,567')).toBeInTheDocument();
  });

  it('keeps stale data visible when refresh fails', async () => {
    settingsMocks.getSourceHealth
      .mockResolvedValueOnce({
        last_sync_at: null,
        all_healthy: true,
        sources: [
          {
            reader: 'gold',
            last_sync_at: new Date().toISOString(),
            row_count: 3,
            net_value_cny: 999,
            file_path: '/tmp/gold.xlsx',
            file_modified: new Date().toISOString(),
            file_size_bytes: 100,
            file_stale: false,
            status: 'ok',
          },
        ],
      })
      .mockRejectedValueOnce(new Error('boom'));

    const user = userEvent.setup();
    render(<SourceHealthDashboard labelLookup={{ gold: 'Gold Holdings' }} />);

    expect(await screen.findByText('Gold Holdings')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /refresh/i }));

    await waitFor(() => {
      expect(screen.getByText('Failed to load health data')).toBeInTheDocument();
    });
    expect(screen.getByText('Gold Holdings')).toBeInTheDocument();
  });
});
