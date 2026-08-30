import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { BalanceSheet } from '../pages/BalanceSheet';

const balanceSheetApiMocks = vi.hoisted(() => ({
    getSummary: vi.fn(),
    getHistory: vi.fn(),
    getDates: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
    BalanceSheetAPI: balanceSheetApiMocks,
}));

// ResizeObserver mock for Recharts
class ResizeObserverMock {
    observe() { }
    unobserve() { }
    disconnect() { }
}
window.ResizeObserver = ResizeObserverMock;

// Wide-format mock data using CORRECT column prefixes.
// isAssetCol matches: RMB*, 美元*, 创业股权投资*, 投资资产_*, 固定资产_*
// isLiabilityCol matches: 短期负债_*, 长期负债_*
beforeEach(() => {
    balanceSheetApiMocks.getSummary.mockResolvedValue({
        latest_snapshot: '2026-01-31',
        snapshot_count: 24,
        total_rows: 1,
        rows: [
            {
                record_key: 'BS_20260131',
                '投资资产_现金存款': 100000,   // asset
                '投资资产_证券基金': 400000,   // asset  → total assets = 500,000
                '短期负债_信用卡': 10000,      // liability
                '长期负债_房贷': 10000,        // liability → total liabilities = 20,000
            }
        ],
    });

    // History: wide-format items so isAssetCol/isLiabilityCol work on them
    balanceSheetApiMocks.getHistory.mockResolvedValue({
        snapshots: [
            {
                snapshot_date: '2025-12-31',
                items: [{ record_key: 'BS_20251231', '投资资产_证券基金': 480000, '短期负债_信用卡': 25000 }]
            },
            {
                snapshot_date: '2026-01-31',
                items: [{ record_key: 'BS_20260131', '投资资产_证券基金': 500000, '短期负债_信用卡': 20000 }]
            }
        ]
    });
});

describe('Balance Sheet Batch 3', () => {
    it('renders KPI cards with aggregated data', async () => {
        render(<BalanceSheet />);

        // Total Assets = 100,000 + 400,000 = 500,000
        await waitFor(() => {
            expect(screen.getByText('¥500,000')).toBeInTheDocument();
        });

        // Total Liabilities = 10,000 + 10,000 = 20,000
        expect(screen.getByText('¥20,000')).toBeInTheDocument();

        // Net Worth = 500,000 - 20,000 = 480,000
        expect(screen.getByText('¥480,000')).toBeInTheDocument();

        // Snapshot count
        expect(screen.getByText('24')).toBeInTheDocument();
    });

    it('renders detail table grouping assets and liabilities', async () => {
        render(<BalanceSheet />);

        await waitFor(() => {
            expect(screen.queryByText('Coming soon')).not.toBeInTheDocument();
        });

        // Section headers
        expect(screen.getByText('Assets')).toBeInTheDocument();
        expect(screen.getByText('Liabilities')).toBeInTheDocument();

        // Column keys rendered as item names (wide-format — no name remapping)
        expect(screen.getByText('投资资产_现金存款')).toBeInTheDocument();
        expect(screen.getByText('投资资产_证券基金')).toBeInTheDocument();
        expect(screen.getByText('短期负债_信用卡')).toBeInTheDocument();
        expect(screen.getByText('长期负债_房贷')).toBeInTheDocument();
    });
});
