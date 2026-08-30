import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TransactionBrowser } from '../pages/TransactionBrowser';
import { ImportWorkbench } from '../pages/ImportWorkbench';
import { ClassificationRules } from '../pages/ClassificationRules';
import { TierAudit } from '../pages/TierAudit';

// Mock the API
const managementMocks = vi.hoisted(() => ({
    searchTransactions: vi.fn(),
    getTransactionFilters: vi.fn(),
    previewImports: vi.fn(),
}));

const taxonomyMocks = vi.hoisted(() => ({
    getRules: vi.fn(),
    getClasses: vi.fn(),
    getTiers: vi.fn(),
    getAssetAudit: vi.fn(),
    deleteRule: vi.fn(),
    runAutoTag: vi.fn(),
}));

const operationsMocks = vi.hoisted(() => ({
    getSyncHistory: vi.fn(),
    getSyncHistoryDetail: vi.fn(),
}));

const coreApiMocks = vi.hoisted(() => ({
    startSync: vi.fn(),
}));

vi.mock('../src/services/api', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        ManagementAPI: managementMocks,
        TaxonomyAPI: taxonomyMocks,
        OperationsAPI: operationsMocks,
        api: {
            ...actual.api,
            startSync: coreApiMocks.startSync,
        },
    };
});

describe('Management P2 UI Smoke Tests', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    describe('Transaction Browser', () => {
        it('renders and fetches transactions', async () => {
            managementMocks.searchTransactions.mockResolvedValue({
                transactions: [{ id: 1, transaction_date: '2023-01-01', asset_id: 'AAPL', quantity: 10, amount_net: 1000, price_unit: 100, commission_fee: 0, currency: 'USD', account: 'ACC', memo: '', transaction_type: 'buy', source_system: 'schwab', verified: true }],
                total: 1
            });
            managementMocks.getTransactionFilters.mockResolvedValue({ sources: [], raw_types: [], normalized_types: [], accounts: [] });

            render(
                <MemoryRouter>
                    <TransactionBrowser />
                </MemoryRouter>
            );

            expect(screen.getByText('Transaction Evidence')).toBeInTheDocument();
            await waitFor(() => {
                expect(managementMocks.searchTransactions).toHaveBeenCalled();
            });
            expect(screen.getByText('Detail View')).toBeInTheDocument();
            expect(screen.getByText('TRX-1-AAPL')).toBeInTheDocument();
        });
    });

    describe('Import Workbench', () => {
        it('renders with preview button', async () => {
            operationsMocks.getSyncHistory.mockResolvedValue({ runs: [] });
            operationsMocks.getSyncHistoryDetail.mockResolvedValue(null);
            coreApiMocks.startSync.mockResolvedValue({ status: 'started' });
            render(<ImportWorkbench />);

            expect(await screen.findByText('Sync / Import History')).toBeInTheDocument();
            expect(screen.getByText('Run Preview')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /refresh runs/i })).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument();
            expect(operationsMocks.getSyncHistory).toHaveBeenCalled();
        });
    });

    describe('Classification Rules', () => {
        it('renders and fetches rules', async () => {
            taxonomyMocks.getRules.mockResolvedValue(
                [{ id: 1, rule_type: 'exact_id', pattern: 'TEST', priority: 10, class_name: 'Equity' }]
            );
            taxonomyMocks.getClasses.mockResolvedValue([]);

            render(<ClassificationRules />);

            expect(screen.getByText('Classification Rules')).toBeInTheDocument();
            await waitFor(() => {
                expect(taxonomyMocks.getRules).toHaveBeenCalled();
            });
            expect(screen.getByText('TEST')).toBeInTheDocument();
        });
    });

    describe('Tier Audit', () => {
        it('renders and fetches tier data', async () => {
            taxonomyMocks.getTiers.mockResolvedValue([{ id: '1', name: 'Equity', name_en: 'Equity', target_pct: 60, color: null }]);
            taxonomyMocks.getAssetAudit.mockResolvedValue({ assets: [], total: 0 });

            render(<TierAudit />);

            // Initially shows loading state
            expect(screen.getByText('Loading tier audit...')).toBeInTheDocument();
            // After data loads, shows title
            await waitFor(() => {
                expect(screen.getByText('Tier Audit')).toBeInTheDocument();
            });
            expect(taxonomyMocks.getTiers).toHaveBeenCalled();
        });
    });
});
