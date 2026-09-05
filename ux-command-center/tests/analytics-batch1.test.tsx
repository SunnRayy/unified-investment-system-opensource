import React from 'react';
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '../test-utils';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../src/theme/useTheme';

// Import components - these might not exist yet, which will cause a compile error, 
// strictly speaking that is a specific kind of "failing test" in TDD (compilation failure).
// To make it run and fail on assertions (runtime failure), we would usually need the files to exist.
// However, since we are doing strict TDD, we expect this file to cause errors immediately.
// If the components don't exist, we can't really "run" the test to see assertions fail, 
// the test runner will just crash.
// To follow "RED" properly when files don't exist, we can stub them or expect the build to fail.
// Given the instructions, I will try to import them. If modules are missing, that counts as RED.

import { BalanceSheet } from '../pages/BalanceSheet';
import { IncomeExpense } from '../pages/IncomeExpense';
import { Analytics } from '../pages/Analytics';
import { Layout } from '../components/Layout';

// Mock the API to avoid network calls during render
vi.mock('../src/services/api', () => ({
    api: {
        getKPI: vi.fn(),
        getDashboardActions: vi.fn(),
        getVerificationTrends: vi.fn(),
    },
    BalanceSheetAPI: { getSummary: vi.fn() },
    IncomeExpenseAPI: { getSummary: vi.fn() },
    AnalyticsAPI: {
        getProjection: vi.fn(),
        getProjectionDefaults: vi.fn().mockResolvedValue({}),
        getCashFlowTrends: vi.fn(),
        listGoals: vi.fn(),
    },
    // test-utils' render() wraps every test in CurrencyProvider, which imports
    // API_BASE from services/api/base — this mock replaces the whole barrel
    // (services/api), so it must still provide that re-exported constant.
    API_BASE: '/api',
}));

describe('Phase 7.5 Batch 1 Smoke Tests', () => {
    // Test 1: BalanceSheet stub renders
    test('BalanceSheet route renders', () => {
        render(<BalanceSheet />);
        expect(screen.getByTestId('balance-sheet-page')).toBeInTheDocument();
        expect(screen.getByText('Balance Sheet')).toBeInTheDocument();
    });

    // Test 2: IncomeExpense stub renders
    test('IncomeExpense route renders', () => {
        render(<IncomeExpense />);
        expect(screen.getByTestId('income-expense-page')).toBeInTheDocument();
        expect(screen.getByText('Income & Expense')).toBeInTheDocument();
    });

    // Test 3: Analytics stub renders
    test('Analytics route renders', () => {
        render(<Analytics />);
        expect(screen.getByTestId('analytics-page')).toBeInTheDocument();
        expect(screen.getByText(/Analytics/)).toBeInTheDocument();
    });

    // Test 4: Nav has 4 section headings
    test('Nav has 4 sections', () => {
        render(
            <ThemeProvider>
                <Layout><div>test</div></Layout>
            </ThemeProvider>
        );
        expect(screen.getByText('Portfolio')).toBeInTheDocument();
        expect(screen.getByText('Reports')).toBeInTheDocument();
        expect(screen.getByText('Operations')).toBeInTheDocument();
        expect(screen.getByText('Management')).toBeInTheDocument();
    });
});
