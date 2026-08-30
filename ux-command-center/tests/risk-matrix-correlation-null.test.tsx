import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import { RiskMatrix } from '../pages/RiskMatrix';
import { api } from '../src/services/api';

vi.mock('../src/context/usePortfolioFilter', () => ({
  usePortfolioFilter: () => ({
    includeNonRebalanceable: false,
    toggleNonRebalanceable: vi.fn(),
  }),
}));

vi.mock('../src/services/api', () => ({
  api: {
    getRiskMetrics: vi.fn(),
    getRiskCorrelation: vi.fn(),
  },
  ExportAPI: {
    downloadAiContext: vi.fn(),
  },
}));

describe('RiskMatrix correlation null rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getRiskMetrics as ReturnType<typeof vi.fn>).mockResolvedValue({
      volatility: 12.1,
      volatility_status: 'MED',
      sharpe: 0.5,
      sharpe_status: 'AVG',
      var_95: 1.2,
      var_95_status: 'LOW',
      beta: 1.0,
      div_score: 6,
    });
    (api.getRiskCorrelation as ReturnType<typeof vi.fn>).mockResolvedValue({
      method: 'empirical_holdings',
      assets: ['Equity', 'Fixed Income'],
      matrix: [
        {
          asset: 'Equity',
          correlations: {
            Equity: { value: 1.0, overlap: 12, low_confidence: false },
            'Fixed Income': { value: null, overlap: 4, low_confidence: false },
          },
        },
        {
          asset: 'Fixed Income',
          correlations: {
            Equity: { value: null, overlap: 4, low_confidence: false },
            'Fixed Income': { value: 1.0, overlap: 12, low_confidence: false },
          },
        },
      ],
      insufficient_pairs: 1,
      total_pairs: 1,
      effective_periods: 2,
      overlap_min: 0,
      overlap_median: 0,
      min_overlap_periods: 8,
      window_start: '2025-09-15',
      window_end: '2026-03-12',
      excluded_jump_points_count: 2,
      winsor_p_low: 0.05,
      winsor_p_high: 0.95,
    });
  });

  it('renders null correlations as dash and shows coverage warning/footnote', async () => {
    render(<RiskMatrix />);

    await waitFor(() => {
      expect(api.getRiskCorrelation).toHaveBeenCalled();
    });

    expect(screen.getByText('Most class pairs lack sufficient history for correlation. Results shown where available.')).toBeInTheDocument();
    expect(screen.getByText(/Window: 2025-09-15 to 2026-03-12/i)).toBeInTheDocument();
    expect(screen.getByText('0.00 = near-zero correlation computed from data; – = insufficient overlapping return periods.')).toBeInTheDocument();
    expect(screen.getAllByText('–').length).toBeGreaterThan(0);
  });

  it('renders low-confidence badge when backend marks a cell as low confidence', async () => {
    (api.getRiskCorrelation as ReturnType<typeof vi.fn>).mockResolvedValue({
      method: 'empirical_holdings',
      assets: ['Equity', 'Fixed Income'],
      matrix: [
        {
          asset: 'Equity',
          correlations: {
            Equity: { value: 1.0, overlap: 12, low_confidence: false },
            'Fixed Income': { value: 0.22, overlap: 9, low_confidence: true },
          },
        },
        {
          asset: 'Fixed Income',
          correlations: {
            Equity: { value: 0.22, overlap: 9, low_confidence: true },
            'Fixed Income': { value: 1.0, overlap: 12, low_confidence: false },
          },
        },
      ],
      insufficient_pairs: 0,
      total_pairs: 1,
      effective_periods: 12,
      overlap_min: 9,
      overlap_median: 9,
      min_overlap_periods: 8,
      window_start: '2025-09-15',
      window_end: '2026-03-12',
      excluded_jump_points_count: 0,
      winsor_p_low: 0.05,
      winsor_p_high: 0.95,
    });

    render(<RiskMatrix />);

    await waitFor(() => {
      expect(api.getRiskCorrelation).toHaveBeenCalled();
    });

    expect(screen.getAllByText('LC').length).toBeGreaterThan(0);
  });
});
