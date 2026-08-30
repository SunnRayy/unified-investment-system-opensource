import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import { Performance } from '../pages/Performance';
import { api } from '../src/services/api';

vi.mock('recharts', () => ({
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    AreaChart: () => <div data-testid="area-chart" />,
    Area: () => null,
    BarChart: () => <div data-testid="bar-chart" />,
    Bar: () => null,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    Cell: () => null,
    ReferenceLine: () => null,
}));

vi.mock('../src/context/usePortfolioFilter', () => ({
    usePortfolioFilter: () => ({
        includeNonRebalanceable: false,
        toggleNonRebalanceable: vi.fn(),
    }),
}));

vi.mock('../src/services/api', () => ({
    api: {
        getPerformanceHistory: vi.fn(),
        getPerformanceSummary: vi.fn(),
        getGainsAnalysis: vi.fn(),
        getPerformanceByClass: vi.fn(),
        getReturns: vi.fn(),
        getAttribution: vi.fn(),
        getPerformanceRiskMetrics: vi.fn(),
    },
}));

describe('Performance Risk Metrics Guards', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        (api.getPerformanceHistory as ReturnType<typeof vi.fn>).mockResolvedValue([]);
        (api.getPerformanceSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
            net_worth: 1000000,
            total_cost_basis: 900000,
            total_unrealized_pl: 100000,
            unrealized_pl_pct: 11.11,
            total_realized_pl: 0,
            total_lifetime_pl: 100000,
            asset_count: 10,
            snapshot_date: '2026-03-10',
        });
        (api.getGainsAnalysis as ReturnType<typeof vi.fn>).mockResolvedValue({ assets: [] });
        (api.getPerformanceByClass as ReturnType<typeof vi.fn>).mockResolvedValue({
            top_classes: [],
            sub_classes: [],
            total_market_value: 0,
            total_cost_basis: 0,
        });
        (api.getReturns as ReturnType<typeof vi.fn>).mockResolvedValue({
            twr_cumulative: null,
            twr_ytd: null,
            twr_1y: null,
            mwr_xirr: null,
        });
        (api.getAttribution as ReturnType<typeof vi.fn>).mockResolvedValue({
            portfolio_return: 0,
            benchmark_return: 0,
            excess_return: 0,
            total_allocation_effect: 0,
            total_selection_effect: 0,
            total_interaction_effect: 0,
            classes: [],
        });
    });

    it('isolates backend error payload and does not render risk cards', async () => {
        (api.getPerformanceRiskMetrics as ReturnType<typeof vi.fn>).mockResolvedValue({
            error: 'backend failure',
        });

        render(<Performance />);
        fireEvent.click(screen.getByRole('button', { name: /Risk Metrics/i }));

        await waitFor(() => {
            expect(api.getPerformanceRiskMetrics).toHaveBeenCalled();
        });

        expect(screen.getByText('Performance Analysis')).toBeInTheDocument();
        expect(screen.getByText('No risk metrics data available')).toBeInTheDocument();
        expect(screen.queryByText('Sharpe Ratio')).not.toBeInTheDocument();
        expect(screen.queryByText('Annual Volatility')).not.toBeInTheDocument();
    });

    it('renders fallback text instead of crashing on undefined/NaN metric fields', async () => {
        (api.getPerformanceRiskMetrics as ReturnType<typeof vi.fn>).mockResolvedValue({
            max_drawdown: undefined,
            sharpe_ratio: undefined,
            sortino_ratio: Number.NaN,
            calmar_ratio: undefined,
            volatility_annual: 18.5,
            total_return: undefined,
            data_points: 12,
        });

        render(<Performance />);
        fireEvent.click(screen.getByRole('button', { name: /Risk Metrics/i }));

        await waitFor(() => {
            expect(api.getPerformanceRiskMetrics).toHaveBeenCalled();
        });

        expect(screen.getByText('Sharpe Ratio')).toBeInTheDocument();
        expect(screen.getAllByText('Insufficient data').length).toBeGreaterThan(0);
    });
});
