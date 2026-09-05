import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '../test-utils';
import { Dashboard } from '../pages/Dashboard';

const apiMocks = vi.hoisted(() => ({
    getKPI: vi.fn(),
    getInsights: vi.fn(),
    getAllocation: vi.fn(),
    getAuditSummary: vi.fn(),
    getPerformanceHistory: vi.fn(),
    getReturns: vi.fn(),
    getDashboardActions: vi.fn(),
}));

const sentimentApiMocks = vi.hoisted(() => ({
    getCached: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
    api: apiMocks,
    SentimentAPI: sentimentApiMocks,
    ExportAPI: {
        downloadAiContext: vi.fn(),
    },
    // test-utils' render() wraps every test in CurrencyProvider, which imports
    // API_BASE from services/api/base — this mock replaces the whole barrel
    // (services/api), so it must still provide that re-exported constant.
    API_BASE: '/api',
}));

vi.mock('../src/context/usePortfolioFilter', () => ({
    usePortfolioFilter: () => ({
        includeNonRebalanceable: false,
        setIncludeNonRebalanceable: vi.fn(),
    }),
    // test-utils' render() wraps every test in the real PortfolioFilterProvider;
    // this mock replaces the whole module, so it must also provide a
    // pass-through for that wrapper to render, or nothing here would exist to import.
    PortfolioFilterProvider: ({ children }: { children: React.ReactNode }) => children,
}));

beforeEach(() => {
    apiMocks.getKPI.mockResolvedValue({
        net_worth: 1000000,
        pnl_24h: 1200,
        market_pulse: 62,
        market_pulse_sentiment: 'Neutral',
    });
    apiMocks.getInsights.mockResolvedValue([]);
    apiMocks.getAllocation.mockResolvedValue([]);
    apiMocks.getAuditSummary.mockResolvedValue({
        total_logs: 0,
        last_sync_timestamp: '2026-02-14T10:00:00Z',
        unresolved_conflicts: 0,
    });
    apiMocks.getPerformanceHistory.mockResolvedValue([
        { name: '2026-02-15', value: 950000 },
        { name: '2026-03-15', value: 1000000 },
    ]);
    apiMocks.getDashboardActions.mockResolvedValue({ actions: [] });

    apiMocks.getReturns.mockResolvedValue({
        twr_ytd: 12.3,
        mwr_xirr: 8.7,
    });

    sentimentApiMocks.getCached.mockResolvedValue({
        indicators: [
            { section: 'equity_macro', zone_color: 'green' },
            { section: 'equity_macro', zone_color: 'red' },
            { section: 'gold', zone_color: 'yellow' },
            { section: 'crypto', zone_color: 'orange' },
        ],
    });
});

describe('Dashboard Batch 4 - Top Row 3-Card KPI', () => {
    it('renders portfolio, 30-day change, and market status cards', async () => {
        render(<Dashboard />);

        await waitFor(() => {
            expect(screen.getByText('Portfolio')).toBeInTheDocument();
        });

        expect(screen.getByText('VS LAST MONTH')).toBeInTheDocument();
        expect(screen.getByText('Since Feb 2026')).toBeInTheDocument();
        expect(screen.getByText('Market Status')).toBeInTheDocument();
        expect(screen.getByText('YTD')).toBeInTheDocument();
        expect(screen.getByText('XIRR')).toBeInTheDocument();
        expect(screen.getByText('+12.3%')).toBeInTheDocument();
        expect(screen.getByText('+8.7%')).toBeInTheDocument();
        expect(screen.getByText('Cautious')).toBeInTheDocument();
        expect(screen.getByText(/1 of 4 indicators in red/)).toBeInTheDocument();

        expect(screen.queryByText('Market Regime')).not.toBeInTheDocument();
        expect(screen.queryByText('Market Pulse')).not.toBeInTheDocument();
        expect(screen.queryByText('30-Day Change')).not.toBeInTheDocument();
        expect(screen.queryByText('VIX')).not.toBeInTheDocument();
    });
});
