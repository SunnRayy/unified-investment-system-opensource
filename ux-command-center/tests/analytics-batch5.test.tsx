import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '../test-utils';
import { Analytics } from '../pages/Analytics';

const analyticsApiMocks = vi.hoisted(() => ({
    getProjection: vi.fn(),
    getProjectionDefaults: vi.fn().mockResolvedValue({
        suggested_return: 0.08,
        suggested_volatility: 0.15,
        avg_monthly_investment_12m: 20000,
        avg_monthly_investment_36m: 18000,
        suggested_contribution_run_rate: 20000,
    }),
    getCashFlowTrends: vi.fn(),
    getCashFlowForecast: vi.fn(),
    listGoals: vi.fn(),
    createGoal: vi.fn(),
    deleteGoal: vi.fn(),
    getGoalProbability: vi.fn(),
    getMarketRegime: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
    AnalyticsAPI: analyticsApiMocks,
    // test-utils' render() wraps every test in CurrencyProvider, which imports
    // API_BASE from services/api/base — this mock replaces the whole barrel
    // (services/api), so it must still provide that re-exported constant.
    API_BASE: '/api',
}));

// ResizeObserver mock for Recharts
class ResizeObserverMock {
    observe() { }
    unobserve() { }
    disconnect() { }
}
window.ResizeObserver = ResizeObserverMock;

beforeEach(() => {
    vi.clearAllMocks();

    analyticsApiMocks.getProjection.mockResolvedValue({
        years: [0, 1, 2],
        percentiles: {
            p10: [1000, 1050, 1100],
            p25: [1000, 1070, 1150],
            p50: [1000, 1100, 1200],
            p75: [1000, 1130, 1250],
            p90: [1000, 1150, 1300],
        },
        final_value_stats: { mean: 1200, median: 1200, std: 50, min: 1100, max: 1300 },
        assumptions: { annual_return: 0.07, annual_volatility: 0.15, annual_contribution: 0, num_simulations: 1000 },
        goal_probability: 0.85,
        goal_target: 1150,
    });

    analyticsApiMocks.getCashFlowTrends.mockResolvedValue({
        monthly: [
            { month: '2026-01', total_income: 60000, total_expense: 20000, net: 40000 }
        ],
        trends: {
            avg_income: 60000,
            avg_expense: 20000,
            avg_net: 40000,
            savings_rate: 66.0, // already-percent, per cash_flow.py::calculate_trends contract — NOT a 0-1 fraction
            months_analyzed: 1,
        }
    });

    analyticsApiMocks.getCashFlowForecast.mockResolvedValue({
        months: 6,
        income_forecast: [61000, 62000],
        expense_forecast: [21000, 22000],
        net_forecast: [40000, 40000],
    });

    analyticsApiMocks.listGoals.mockResolvedValue([
        {
            id: 1,
            name: 'Retirement Fund',
            target_amount: 5000000,
            target_date: '2046-01-01',
            current_amount: 1200000,
            monthly_contribution: 10000,
            goal_type: 'retirement',
            status: 'active',
            notes: null,
            created_at: '2026-01-01T00:00:00Z',
            months_remaining: 240
        }
    ]);

    analyticsApiMocks.getGoalProbability.mockResolvedValue({
        goal_id: 1,
        probability: 0.72
    });
});

describe('Analytics Batch 5', () => {
    // R-5 (docs/plans/2026-07-25-forecast-planning-redesign.md §3, §6b(e)):
    // tabs collapsed 4 → 2 — "Your Path" (merges the former Projections /
    // Cash Flow / North Star tabs into one scrolling narrative) and "Goals".
    // Defaults to "Your Path".
    it('renders 2 tabs and defaults to Your Path', async () => {
        render(<Analytics />);
        expect(screen.getByRole('button', { name: /Your Path/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Goals/i })).toBeInTheDocument();

        // Defaults to Your Path controls (what-if tool lives in this tab now)
        expect(screen.getByText('Years:')).toBeInTheDocument();
        // Wait for loading to finish
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /Run Projection/i })).toBeInTheDocument();
        });
    });

    it('renders the what-if tool on Your Path (default tab) with fan chart data', async () => {
        render(<Analytics />);

        // Wait for projection to be called and data rendered
        await waitFor(() => {
            expect(analyticsApiMocks.getProjection).toHaveBeenCalled();
        });

        // Check stats from final_value_stats
        expect(screen.getAllByText('¥1,200').length).toBeGreaterThan(0); // Mean/Median (at least one)
        expect(screen.getByText('85.0%')).toBeInTheDocument(); // Goal Probability
    });

    // R-5: Cash Flow is no longer a separate tab — its content (Net New
    // Invested / RSU / Predictive Forecast) now lives inside "Your Path",
    // which is already the default tab, so no tab click is needed here.
    it('renders the Cash Flow detail section within Your Path', async () => {
        render(<Analytics />);

        await waitFor(() => {
            expect(analyticsApiMocks.getCashFlowTrends).toHaveBeenCalled();
            expect(analyticsApiMocks.getCashFlowForecast).toHaveBeenCalled();
        });

        expect(screen.getByText('Cash Flow Trends')).toBeInTheDocument();
        expect(screen.getByText('Cash Flow Forecast')).toBeInTheDocument();
        expect(screen.getAllByText('¥60,000').length).toBeGreaterThan(0); // Avg Income
    });

    it('renders Goals tab with goal cards', async () => {
        render(<Analytics />);
        const goalsBtn = screen.getByRole('button', { name: /Goals/i });
        fireEvent.click(goalsBtn);

        await waitFor(() => {
            expect(analyticsApiMocks.listGoals).toHaveBeenCalled();
        });

        expect(screen.getByText('Retirement Fund')).toBeInTheDocument();
        expect(screen.getByText('¥5,000,000')).toBeInTheDocument(); // Target
        expect(screen.getByText('¥1,200,000')).toBeInTheDocument(); // Current
        expect(screen.getByRole('button', { name: /\+ Add Goal/i })).toBeInTheDocument();

        await waitFor(() => {
            // goal probability loaded
            expect(screen.getByText('72.0%')).toBeInTheDocument();
        });
    });

    it('shows cash flow average badge in Projections and allows click-to-fill contribution', async () => {
        render(<Analytics />);

        await waitFor(() => {
            expect(analyticsApiMocks.getCashFlowTrends).toHaveBeenCalled();
            expect(analyticsApiMocks.getCashFlowForecast).toHaveBeenCalled();
        });

        const badge = await screen.findByText('CF avg: ¥40,000/mo ↗');
        expect(badge).toBeInTheDocument();

        const contributionInput = screen.getByLabelText(/Contribution/i);
        fireEvent.change(contributionInput, { target: { value: '1234' } });
        expect(contributionInput).toHaveValue(1234);

        fireEvent.click(badge);
        expect(contributionInput).toHaveValue(40000);
    });

    it('adds run projection button on each goal card and pre-fills projection inputs', async () => {
        render(<Analytics />);
        fireEvent.click(screen.getByRole('button', { name: /Goals/i }));

        await waitFor(() => {
            expect(analyticsApiMocks.listGoals).toHaveBeenCalled();
        });

        const runProjectionButton = await screen.findByRole('button', { name: /Run Projection/i });
        fireEvent.click(runProjectionButton);

        await waitFor(() => {
            expect(screen.getByText('Monte Carlo Forecast')).toBeInTheDocument();
        });

        const yearsInput = screen.getByLabelText(/Years/i);
        const contributionInput = screen.getByLabelText(/Contribution/i);
        const goalInput = screen.getByLabelText(/Goal/i);

        expect(yearsInput).toHaveValue(20);
        expect(contributionInput).toHaveValue(10000);
        expect(goalInput).toHaveValue(5000000);

        await waitFor(() => {
            expect(analyticsApiMocks.getProjection).toHaveBeenCalledTimes(2);
        });

        const lastCall = analyticsApiMocks.getProjection.mock.calls[1][0];
        expect(lastCall).toMatchObject({
            years: '20',
            annual_contribution: '10000',
            goal_target: '5000000',
        });
    });
});
