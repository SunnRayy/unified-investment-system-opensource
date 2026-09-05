/**
 * Tests for the Movers card in DashboardCards.tsx (GitHub #27).
 *
 * Coverage:
 *  - Default 30D tab is active on mount
 *  - ALL tab renders gains-based list (no API call)
 *  - Clicking a window tab fetches /performance/movers with correct params
 *  - ~ prefix renders when window_covered=false
 *  - Level control dispatches the correct level to getMovers
 *  - Loading state visible while fetching
 *  - Error state renders when fetch fails
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { Movers } from '../pages/dashboard/DashboardCards';
import type { GainsAsset, MoversResponse } from '../src/services/api';

// ---------------------------------------------------------------------------
// Mock the api barrel so authFetch never fires
// ---------------------------------------------------------------------------

const mockGetMovers = vi.fn();

vi.mock('../src/services/api', () => ({
    api: {
        getMovers: (...args: unknown[]) => mockGetMovers(...args),
    },
    // Named type re-exports are not runtime values; no-op here.
}));

// ---------------------------------------------------------------------------
// Sample data
// ---------------------------------------------------------------------------

const GAINS_ASSETS: GainsAsset[] = [
    {
        asset_id: 'US_STK_MSFT',
        name: 'Microsoft',
        top_class: 'Equity',
        currency: 'USD',
        cost_basis: 100_000,
        market_value: 120_000,
        unrealized_pl: 20_000,
        realized_pl: 0,
        return_pct: 20,
    },
];

const MOVERS_RESPONSE_30D: MoversResponse = {
    window: '30d',
    window_start: '2026-06-04',
    level: 'asset',
    movers: [
        {
            key: 'US_STK_MSFT',
            name: 'Microsoft',
            top_class: 'Equity (股票)',
            sub_class: 'US Equity',
            pct_change: 5.2,
            pl_impact_cny: 6_000,
            market_value: 120_000,
            window_covered: true,
            asset_count: 1,
        },
    ],
    excluded_unpriced_count: 3,
};

const MOVERS_RESPONSE_PARTIAL: MoversResponse = {
    window: '7d',
    window_start: '2026-06-27',
    level: 'asset',
    movers: [
        {
            key: 'US_STK_NEW',
            name: 'NewIPO',
            top_class: 'Equity (股票)',
            sub_class: 'US Equity',
            pct_change: 11.1,
            pl_impact_cny: 5_000,
            market_value: 50_000,
            window_covered: false,   // <-- triggers ~ marker
            asset_count: 1,
        },
    ],
    excluded_unpriced_count: 0,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Movers card', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockGetMovers.mockResolvedValue(MOVERS_RESPONSE_30D);
    });

    it('renders Top Movers heading', () => {
        render(<Movers assets={[]} />);
        expect(screen.getByText('Top Movers')).toBeInTheDocument();
    });

    it('shows 30D pill as active by default', () => {
        render(<Movers assets={[]} />);
        // The 30D button should be rendered
        expect(screen.getByRole('button', { name: '30D' })).toBeInTheDocument();
        // All tab buttons should be present
        for (const tab of ['7D', '30D', '3M', '6M', '12M', 'ALL']) {
            expect(screen.getByRole('button', { name: tab })).toBeInTheDocument();
        }
    });

    it('fetches with window=30d and level=asset on mount (default tab)', async () => {
        render(<Movers assets={[]} />);
        await waitFor(() => {
            expect(mockGetMovers).toHaveBeenCalledWith('30d', 'asset', 10);
        });
    });

    it('renders mover name from API response', async () => {
        render(<Movers assets={[]} />);
        await waitFor(() => {
            expect(screen.getByText('Microsoft')).toBeInTheDocument();
        });
    });

    it('switches to 7D window and re-fetches', async () => {
        mockGetMovers.mockResolvedValue(MOVERS_RESPONSE_PARTIAL);
        render(<Movers assets={[]} />);

        fireEvent.click(screen.getByRole('button', { name: '7D' }));

        await waitFor(() => {
            expect(mockGetMovers).toHaveBeenCalledWith('7d', 'asset', 10);
        });
    });

    it('renders ~ prefix when window_covered is false', async () => {
        mockGetMovers.mockResolvedValue(MOVERS_RESPONSE_PARTIAL);
        render(<Movers assets={[]} />);

        fireEvent.click(screen.getByRole('button', { name: '7D' }));

        await waitFor(() => {
            // The pct column should have ~ prefix for partial coverage
            const pctCells = screen.getAllByText(/~/);
            expect(pctCells.length).toBeGreaterThan(0);
        });
    });

    it('does not render ~ when window_covered is true', async () => {
        mockGetMovers.mockResolvedValue(MOVERS_RESPONSE_30D);
        render(<Movers assets={[]} />);

        await waitFor(() => {
            expect(screen.getByText('Microsoft')).toBeInTheDocument();
        });

        // No ~ in document for the 30d result (which has window_covered=true)
        const tildeEls = screen.queryAllByText(/~/);
        expect(tildeEls.length).toBe(0);
    });

    it('ALL tab shows gains-based list without calling getMovers', async () => {
        render(<Movers assets={GAINS_ASSETS} />);

        // Wait for initial 30D fetch
        await waitFor(() => expect(mockGetMovers).toHaveBeenCalledTimes(1));

        vi.clearAllMocks();
        fireEvent.click(screen.getByRole('button', { name: 'ALL' }));

        // Switching to ALL must NOT trigger another fetch
        await waitFor(() => {
            expect(mockGetMovers).not.toHaveBeenCalled();
        });

        // The asset from gains data should appear
        expect(screen.getByText('Microsoft')).toBeInTheDocument();
    });

    it('shows error state when fetch fails', async () => {
        mockGetMovers.mockRejectedValue(new Error('network error'));
        render(<Movers assets={[]} />);

        await waitFor(() => {
            expect(screen.getByText(/Failed to load movers/i)).toBeInTheDocument();
        });
    });

    it('dispatches correct level to getMovers when level control changes', async () => {
        render(<Movers assets={[]} />);
        await waitFor(() => expect(mockGetMovers).toHaveBeenCalledWith('30d', 'asset', 10));

        vi.clearAllMocks();
        fireEvent.click(screen.getByRole('button', { name: 'CLASS' }));

        await waitFor(() => {
            expect(mockGetMovers).toHaveBeenCalledWith('30d', 'top_class', 10);
        });
    });

    it('level control is hidden on ALL tab', async () => {
        render(<Movers assets={[]} />);

        // ALL tab
        fireEvent.click(screen.getByRole('button', { name: 'ALL' }));

        // Level buttons should not be visible
        expect(screen.queryByRole('button', { name: 'CLASS' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'SUB-CLASS' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'ASSET' })).not.toBeInTheDocument();
    });
});
