/**
 * Tests for the provisional pending-trade overlay on the Compass page.
 * Covers:
 *  (a) toggle OFF — default state renders plain allocation without provisional columns
 *  (b) toggle ON — api called with includePending=true, renders provisional banner
 *      and per-row provisional_pct annotation with pending_trade_count note
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { Compass } from '../pages/Compass';

// ── API mocks ────────────────────────────────────────────────────────────────

const apiMocks = vi.hoisted(() => ({
    getCompassSummary: vi.fn(),
    getCompassAllocation: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
    api: apiMocks,
}));

vi.mock('../src/context/usePortfolioFilter', () => ({
    usePortfolioFilter: () => ({
        includeNonRebalanceable: false,
        setIncludeNonRebalanceable: vi.fn(),
    }),
}));

// ── Fixture data ─────────────────────────────────────────────────────────────

const SUMMARY = {
    total_net_worth: 2_000_000,
    drift_index: 3.0,
    classes_in_drift: 1,
    total_classes: 5,
    last_sync_date: '2026-06-19',
    last_sync_source: 'Schwab',
};

const BASE_ALLOCATION_ROW = {
    asset_class: 'Equity',
    current_value: 1_000_000,
    currency: 'CNY',
    current_pct: 50.0,
    target_pct: 45.0,
    drift_pct: 5.0,
    tolerance_pct: 2.5,
    status: 'over' as const,
    is_top_level: true,
    parent_class: null,
};

// Plain list response (include_pending=false)
const PLAIN_ROWS = [BASE_ALLOCATION_ROW];

// Envelope response (include_pending=true)
const ENVELOPE_ROWS = [
    {
        ...BASE_ALLOCATION_ROW,
        provisional_value: 900_000,
        provisional_pct: 42.5,
        provisional_delta_cny: -100_000,
    },
];

const ENVELOPE_RESPONSE = {
    rows: ENVELOPE_ROWS,
    meta: { pending_trade_count: 3, is_provisional: true as const },
};

// ── Test setup ───────────────────────────────────────────────────────────────

beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getCompassSummary.mockResolvedValue(SUMMARY);
    // Default: plain list (toggle OFF)
    apiMocks.getCompassAllocation.mockResolvedValue({ rows: PLAIN_ROWS, meta: null });
});

// ── Tests ────────────────────────────────────────────────────────────────────

describe('Compass pending-trade overlay', () => {
    it('(a) toggle OFF — renders allocation table without provisional columns or banner', async () => {
        render(<Compass />);

        // Wait for compass page to load (use data-testid to avoid ambiguity)
        await waitFor(() => {
            expect(screen.getByTestId('compass-page')).toBeInTheDocument();
        });

        // Provisional banner should NOT be present
        expect(screen.queryByTestId('provisional-banner')).not.toBeInTheDocument();

        // Provisional pct cell for Equity should NOT be present
        expect(screen.queryByTestId('provisional-pct-Equity')).not.toBeInTheDocument();

        // API was called with includePending=false
        expect(apiMocks.getCompassAllocation).toHaveBeenCalledWith(false, false);

        // Toggle exists and is unchecked by default
        const toggle = screen.getByRole('switch');
        expect(toggle).toHaveAttribute('aria-checked', 'false');
    });

    it('(b) toggle ON — calls api with includePending=true, shows provisional banner and per-row annotation', async () => {
        render(<Compass />);

        // Wait for initial load
        await waitFor(() => {
            expect(screen.getByTestId('compass-page')).toBeInTheDocument();
        });

        // Reconfigure mock to return envelope when includePending=true
        apiMocks.getCompassAllocation.mockResolvedValue(ENVELOPE_RESPONSE);

        // Click the label checkbox (the hidden <input type="checkbox"> inside the label)
        const checkbox = screen.getByRole('checkbox');
        fireEvent.click(checkbox);

        // Wait for re-fetch with pending=true
        await waitFor(() => {
            expect(apiMocks.getCompassAllocation).toHaveBeenCalledWith(false, true);
        });

        // Provisional banner should be visible
        await waitFor(() => {
            expect(screen.getByTestId('provisional-banner')).toBeInTheDocument();
        });

        // pending_trade_count is shown correctly
        const countEl = screen.getByTestId('pending-trade-count');
        expect(countEl.textContent).toBe('3');

        // Provisional pct for Equity row is rendered
        const provPctEl = screen.getByTestId('provisional-pct-Equity');
        expect(provPctEl.textContent).toContain('42.50%');

        // The "est." badge is rendered inside the provisional cell
        expect(provPctEl.textContent).toContain('est.');

        // Verified current_pct is still present (not replaced by provisional)
        expect(screen.getByText('50.00%')).toBeInTheDocument();

        // Toggle reflects active state
        const toggle = screen.getByRole('switch');
        expect(toggle).toHaveAttribute('aria-checked', 'true');
    });
});
