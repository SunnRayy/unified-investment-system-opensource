import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { IncomeExpense } from '../pages/IncomeExpense';

// Only mock the APIs actually used by the component: IncomeExpenseAPI (rows /
// history) and api.getContributions (GET /north-star/contributions — the
// backend-classified savings-rate basis; the page no longer derives that rate
// from Chinese column-name prefixes).
// AnalyticsAPI.getCashFlowTrends is no longer used by this component.
const incomeExpenseApiMocks = vi.hoisted(() => ({
    getSummary: vi.fn(),
    getHistory: vi.fn(),
    getDates: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
    getContributions: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
    IncomeExpenseAPI: incomeExpenseApiMocks,
    api: apiMocks,
}));

// ResizeObserver mock for Recharts
class ResizeObserverMock {
    observe() { }
    unobserve() { }
    disconnect() { }
}
window.ResizeObserver = ResizeObserverMock;

// Wide-format mock data using CORRECT column prefixes:
//   Income:     收入_*
//   Expense:    必要开支_*  /  非必要开支_*
//   Investment: 投资理财_*
beforeEach(() => {
    incomeExpenseApiMocks.getSummary.mockResolvedValue({
        latest_month: '2026-01',
        month_count: 12,
        total_rows: 1,
        rows: [
            {
                record_key: 'IE_20260101',
                '收入_薪水': 50000,
                '收入_奖金': 10000,
                '必要开支_房租': 15000,
                '必要开支_餐饮': 5000,
            }
        ],
    });

    // History mock: 3 months with varying income/expense — used for chart and averages.
    // Averages: income=(60000+65000+60000)/3=61,667  expense=(20000+25000+20000)/3=21,667  net=40,000
    incomeExpenseApiMocks.getHistory.mockResolvedValue({
        months: [
            { month: '2026-01-01', items: [{ record_key: 'IE_20260101', '收入_薪水': 60000, '必要开支_房租': 20000 }] },
            { month: '2025-12-01', items: [{ record_key: 'IE_20251201', '收入_薪水': 65000, '必要开支_房租': 25000 }] },
            { month: '2025-11-01', items: [{ record_key: 'IE_20251101', '收入_薪水': 60000, '必要开支_房租': 20000 }] },
        ]
    });

    // Backend-classified basis. Deliberately NOT equal to the page's own
    // prefix-derived income/expense (60,000 / 20,000) so the assertions below
    // prove the rate is read from the API, not re-derived client-side.
    apiMocks.getContributions.mockResolvedValue({
        ytd_sum: 0, trailing_12m_sum: 0, unclassified_count: 0,
        by_classification: { external_contribution: 0, internal_transfer: 0, income_reinvested: 0 },
        investment: {
            series: [
                { month: '2025-12', by_destination: { cn_fund: 1000 }, gross_invested: 1000, redemptions: 0, income_basis: 40000, expense_basis: 20000, pass_through_in: 0, pass_through_out: 0, income: 40000 },
                { month: '2026-01', by_destination: { cn_fund: 2000 }, gross_invested: 2000, redemptions: 0, income_basis: 50000, expense_basis: 15000, pass_through_in: 0, pass_through_out: 0, income: 50000 },
            ],
            gross_invested_ttm: 3000, redemptions_ttm: 0, income_ttm: 90000,
            net_external_ttm: 3000, internal_realloc_ttm: 0,
            income_basis_ttm: 90000, expense_basis_ttm: 35000,
            rsu_retained_ttm: 0, investment_numerator_ttm: 3000,
            pass_through_in_ttm: 0, pass_through_out_ttm: 0,
            savings_rate_ttm: 0.602507, investment_rate_ttm: 0.415615,
            undeployed_cash_ttm: 52000,
            by_destination_ttm: { cn_fund: 3000 },
            window_start_month: '2025-12', window_end_month: '2026-01',
            months_with_contribution: 2, months_with_contribution_window: 2,
        },
    });
});

describe('Income & Expense Batch 4', () => {
    it('renders KPI cards derived from summary row', async () => {
        render(<IncomeExpense />);

        await waitFor(() => {
            expect(screen.getByText('Latest Month Income')).toBeInTheDocument();
        });

        // latestIncome = 50000 + 10000 = 60,000
        expect(screen.getByText('¥60,000')).toBeInTheDocument();
        // netSavings = 60000 - 20000 = 40,000 (¥40,000 appears in KPI and averages)
        expect(screen.getAllByText('¥40,000').length).toBeGreaterThan(0);
        // Savings rate comes from the backend basis for 2026-01:
        // (50,000 − 15,000) / 50,000 = 70.0%. The retired client-side prefix
        // derivation would have said 40,000/60,000 = 66.7%.
        expect(screen.getByText('70.0%')).toBeInTheDocument();
        expect(screen.queryByText('66.7%')).not.toBeInTheDocument();
        expect(screen.getByText('Savings Rate (2026-01)')).toBeInTheDocument();
        // Both TTM rates surfaced — they measure different things (§WS-G)
        expect(screen.getByText(/60\.3% saved/)).toBeInTheDocument();
        expect(screen.getByText(/41\.6% reached an investment account/)).toBeInTheDocument();
    });

    it('renders an em-dash (never NaN%/0%) when the contributions basis is unavailable', async () => {
        apiMocks.getContributions.mockRejectedValue(new Error('backend down'));

        render(<IncomeExpense />);

        await waitFor(() => {
            expect(screen.getByText('Savings Rate (latest month)')).toBeInTheDocument();
        });
        expect(screen.getByText('—')).toBeInTheDocument();
        expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
        expect(screen.getByText(/12m: — saved · — reached an investment account/)).toBeInTheDocument();
    });

    it('renders detail table with correct wide-format column grouping', async () => {
        render(<IncomeExpense />);

        await waitFor(() => {
            // stripPrefix('收入_薪水') = '薪水'
            expect(screen.getByText('薪水')).toBeInTheDocument();
        });

        expect(screen.getByText('¥50,000')).toBeInTheDocument();
        // stripPrefix('必要开支_房租') = '房租'
        expect(screen.getByText('房租')).toBeInTheDocument();
        expect(screen.getByText('¥15,000')).toBeInTheDocument();
        // No 投资理财_* columns in mock → shows empty state
        expect(screen.getByText('No investment records')).toBeInTheDocument();
    });

    it('renders averages computed from history data', async () => {
        render(<IncomeExpense />);

        await waitFor(() => {
            expect(screen.getByText('Avg Income')).toBeInTheDocument();
        });

        // (60000 + 65000 + 60000) / 3 = 61,666.67 → ¥61,667
        expect(screen.getByText('¥61,667')).toBeInTheDocument();
        // (20000 + 25000 + 20000) / 3 = 21,666.67 → ¥21,667
        expect(screen.getByText('¥21,667')).toBeInTheDocument();
        // avg net = 40,000 (already verified above via getAllByText)
        // Months = 3
        expect(screen.getByText('3')).toBeInTheDocument();
    });

    it('renders without crashing on empty data', async () => {
        incomeExpenseApiMocks.getSummary.mockResolvedValue({
            latest_month: null, month_count: 0, total_rows: 0, rows: []
        });
        incomeExpenseApiMocks.getHistory.mockResolvedValue({ months: [] });

        render(<IncomeExpense />);

        await waitFor(() => {
            expect(screen.getByText('No income records')).toBeInTheDocument();
            expect(screen.getByText('No expense records')).toBeInTheDocument();
            expect(screen.getByText('No investment records')).toBeInTheDocument();
        });
    });
});
