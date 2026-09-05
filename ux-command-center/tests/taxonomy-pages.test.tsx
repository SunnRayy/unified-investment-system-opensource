import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TierAudit } from '../pages/TierAudit';
import { AssetAudit } from '../pages/AssetAudit';

const taxonomyMocks = vi.hoisted(() => ({
    getTiers: vi.fn(),
    getAssetAudit: vi.fn(),
    getClasses: vi.fn(),
    runAutoTag: vi.fn(),
    upsertRule: vi.fn(),
    setAssetTier: vi.fn(),
    deactivateAsset: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
    TaxonomyAPI: {
        getTiers: taxonomyMocks.getTiers,
        getAssetAudit: taxonomyMocks.getAssetAudit,
        getClasses: taxonomyMocks.getClasses,
        runAutoTag: taxonomyMocks.runAutoTag,
        upsertRule: taxonomyMocks.upsertRule,
        setAssetTier: taxonomyMocks.setAssetTier,
        deactivateAsset: taxonomyMocks.deactivateAsset,
    },
}));

const TIERS = [
    { id: 'tier_1_core', name: '第一梯队 (底仓/价值型)', name_en: null, target_pct: 50, color: '#3b82f6' },
    { id: 'tier_2_diversification', name: '第二梯队 (辅助/分散)', name_en: null, target_pct: 35, color: '#22c55e' },
    { id: 'tier_3_trading', name: '第三梯队 (交易/择时)', name_en: null, target_pct: 15, color: '#f59e0b' },
];

const ASSETS = [
    { asset_id: 'CN_FUND_000198', asset_name: 'A-Share Fund', asset_class: 'CN Equity', tier: '第一梯队 (底仓/价值型)', class_name: 'CN Equity', class_name_cn: 'A股', parent_class_name: 'Equity', parent_class_name_cn: null, market_value_cny: 2000000, quantity: null, snapshot_date: '2026-02-01', source_system: 'CN_Fund_Excel', market_price: 1.2345, price_currency: 'CNY', price_source: 'akshare_fund' },
    { asset_id: 'US_STK_AAPL', asset_name: 'Apple Inc', asset_class: 'US Equity', tier: '第三梯队 (交易/择时)', class_name: 'US Equity', class_name_cn: null, parent_class_name: 'Equity', parent_class_name_cn: null, market_value_cny: 500000, quantity: 10, snapshot_date: '2026-02-01', source_system: 'Schwab', market_price: 201.25, price_currency: 'USD', price_source: 'yfinance' },
    { asset_id: 'CASH_CNY', asset_name: 'Cash CNY', asset_class: 'Cash Checking', tier: null, class_name: 'Cash Checking', class_name_cn: null, parent_class_name: 'Cash', parent_class_name_cn: null, market_value_cny: 100000, quantity: null, snapshot_date: '2026-02-01', source_system: 'Schwab', market_price: null, price_currency: 'CNY', price_source: null },
];

const CLASSES = [
    { id: 1, name: 'Equity', name_cn: null, level: 0, parent_id: null, sort_order: 1, is_rebalanceable: true, description: null, children: [
        { id: 10, name: 'CN Equity', name_cn: 'A股', level: 1, parent_id: 1, sort_order: 1, is_rebalanceable: true, description: null, children: [] },
    ]},
    { id: 5, name: 'Cash', name_cn: null, level: 0, parent_id: null, sort_order: 5, is_rebalanceable: true, description: null, children: [
        { id: 19, name: 'Cash Checking', name_cn: null, level: 1, parent_id: 5, sort_order: 1, is_rebalanceable: true, description: null, children: [] },
    ]},
];

beforeEach(() => {
    // getTiers returns plain array (api.ts extracts data.tiers internally)
    taxonomyMocks.getTiers.mockResolvedValue(TIERS);
    taxonomyMocks.getAssetAudit.mockResolvedValue({ assets: ASSETS, total: ASSETS.length });
    taxonomyMocks.getClasses.mockResolvedValue(CLASSES);
    taxonomyMocks.setAssetTier.mockResolvedValue({ asset_id: 'CN_FUND_000198', tier: '第二梯队 (辅助/分散)' });
    taxonomyMocks.runAutoTag.mockResolvedValue({ classified: 3, unclassified: 0 });
    taxonomyMocks.deactivateAsset.mockResolvedValue({ asset_id: 'US_STK_AAPL', status: 'deactivated' });
});

describe('TierAudit page', () => {
    it('renders tier cards with correct non-100% current percentages', async () => {
        render(<TierAudit />);
        expect(await screen.findByText('Tier Audit')).toBeInTheDocument();

        // Tier 1: only CN_FUND_000198 (¥2,000,000 of ¥2,600,000 total) = 76.9%
        // Tier 3: only US_STK_AAPL (¥500,000 of ¥2,600,000 total) = 19.2%
        // Tier 2: 0 assets = 0.0%
        // CRITICAL: no tier card should show 100.0% current
        const texts = document.body.textContent || '';
        expect(texts).not.toContain('100.0%');
    });

    it('groups bottom section by tier name not by asset class', async () => {
        render(<TierAudit />);
        await screen.findByText('Tier Audit');

        // Tier names appear in both card headers and accordion group headers (multiple matches expected)
        expect(screen.getAllByText('第一梯队 (底仓/价值型)').length).toBeGreaterThan(0);
        expect(screen.getAllByText('第三梯队 (交易/择时)').length).toBeGreaterThan(0);
        // "Unassigned" group for CASH_CNY with no tier (only appears as group header)
        expect(await screen.findByText('Unassigned')).toBeInTheDocument();
        // "Equity" should NOT appear as an accordion group header (was old behavior)
        // Note: "Equity" may appear in the Class column cells, so we check by button role
        const groupButtons = screen.getAllByRole('button');
        const groupLabels = groupButtons.map(btn => btn.textContent || '');
        expect(groupLabels.some(t => t.includes('Equity') && !t.includes('梯队'))).toBe(false);
    });

    it('shows Run Auto-Assign Tiers button', async () => {
        render(<TierAudit />);
        await screen.findByText('Tier Audit');
        expect(await screen.findByRole('button', { name: /auto.*assign|assign.*tier/i })).toBeInTheDocument();
    });

    it('shows Assign Tier button for each asset row', async () => {
        render(<TierAudit />);
        await screen.findByText('Tier Audit');
        // Each expanded group row should have an Assign/Change Tier button
        const tierButtons = await screen.findAllByRole('button', { name: /assign tier|change tier/i });
        expect(tierButtons.length).toBeGreaterThan(0);
    });
});

describe('AssetAudit page', () => {
    it('renders grouped accordion with parent class as group header', async () => {
        render(<AssetAudit />);
        await screen.findByText('Asset Audit');

        // "Equity" and "Cash" are parent class group headers
        expect(await screen.findByText('Equity')).toBeInTheDocument();
        expect(screen.getByText('Cash')).toBeInTheDocument();
    });

    it('shows Classify/Reclassify button per asset row', async () => {
        render(<AssetAudit />);
        await screen.findByText('Asset Audit');

        // Assets are in expanded groups; Reclassify shown for classified assets
        const buttons = await screen.findAllByRole('button', { name: /classify|reclassify/i });
        expect(buttons.length).toBeGreaterThan(0);
    });

    it('shows market price in original currency plus holdings and price sources', async () => {
        render(<AssetAudit />);
        await screen.findByText('Asset Audit');

        expect(screen.getAllByText('Market Price').length).toBeGreaterThan(0);
        expect(screen.getAllByText('Holdings Source').length).toBeGreaterThan(0);
        expect(screen.getAllByText('Price Source').length).toBeGreaterThan(0);
        expect(screen.getByText('USD 201.25')).toBeInTheDocument();
        expect(screen.getByText('CNY 1.2345')).toBeInTheDocument();
        expect(screen.getByText('yfinance')).toBeInTheDocument();
        expect(screen.getByText('akshare_fund')).toBeInTheDocument();
    });

    it('uses a compact fixed layout for the dense audit table', async () => {
        const { container } = render(<AssetAudit />);
        await screen.findByText('Asset Audit');

        const table = container.querySelector('table');
        expect(table?.className).toContain('table-fixed');

        const nameCell = screen.getByText('Apple Inc').closest('td');
        expect(nameCell?.className).toContain('align-top');
        expect(nameCell?.className).toContain('py-3');

        const priceSourceCell = screen.getByText('akshare_fund').closest('td');
        expect(priceSourceCell?.className).toContain('whitespace-normal');
        expect(priceSourceCell?.className).toContain('break-words');
    });

    it('shows unclassified count in header subtitle when assets have no class', async () => {
        // Add an unclassified asset
        const withUnclassified = [
            ...ASSETS,
            { asset_id: 'UNKNOWN_X', asset_name: 'Unknown Asset', asset_class: null, tier: null, class_name: null, class_name_cn: null, parent_class_name: null, parent_class_name_cn: null, market_value_cny: null, quantity: null, snapshot_date: null, source_system: null, market_price: null, price_currency: null, price_source: null },
        ];
        taxonomyMocks.getAssetAudit.mockResolvedValue({ assets: withUnclassified, total: withUnclassified.length });
        render(<AssetAudit />);
        await screen.findByText('Asset Audit');
        // Header subtitle shows "1 unclassified" badge
        expect(screen.getByText(/1 unclassified/i)).toBeInTheDocument();
    });

    it('deactivates an asset through a real API action', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        render(<AssetAudit />);
        await screen.findByText('Asset Audit');
        const fetchCountBeforeDeactivate = taxonomyMocks.getAssetAudit.mock.calls.length;

        const buttons = await screen.findAllByRole('button', { name: /deactivate/i });
        fireEvent.click(buttons[0]);

        await waitFor(() => {
            expect(taxonomyMocks.deactivateAsset).toHaveBeenCalledWith('CASH_CNY');
        });
        expect(taxonomyMocks.getAssetAudit.mock.calls.length).toBe(fetchCountBeforeDeactivate + 1);
    });
});
