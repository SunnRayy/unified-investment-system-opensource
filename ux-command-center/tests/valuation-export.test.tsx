import React from 'react';
import { render, screen, waitFor, fireEvent } from '../test-utils';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Valuation } from '../pages/Valuation';
import { api } from '../src/services/api';

// The Export button on this page shipped with no onClick for months — a
// download icon that did nothing. These tests exist so it cannot silently
// revert to that: they assert the click produces a real CSV, not merely that a
// handler is attached.

vi.mock('../src/services/api', () => ({
    api: {
        getValuationSnapshots: vi.fn(),
        getValuationMacro: vi.fn(),
        getValuationWatchlist: vi.fn(),
        triggerValuationRefresh: vi.fn(),
        addValuationWatchlist: vi.fn(),
        deleteValuationWatchlist: vi.fn(),
    },
}));

const snapshot = (over: Record<string, unknown> = {}) => ({
    id: 1,
    snapshot_date: '2026-08-30',
    ticker: 'VOO',
    display_name: 'Vanguard S&P 500',
    row_kind: 'holding',
    linked_ticker: null,
    asset_id: 'US_ETF_VOO',
    asset_class: 'US Equity',
    pe_ttm: 24.5,
    pe_forward: 22.1,
    pb_ratio: 4.2,
    peg_ratio: null,
    fcf_yield: 0.031,
    dividend_yield: 0.013,
    ev_ebitda: null,
    sec_yield: null,
    pe_ttm_pct: 0.72,
    pe_fwd_pct: null,
    pb_pct: null,
    percentile_value: 72,
    percentile_metric: 'pe_ttm',
    pct_years: 10,
    valuation_signal: 'neutral',
    signal_basis: 'pe_ttm percentile',
    rate_adjustment_factor: 1,
    data_source: 'akshare',
    is_estimable: true,
    notes: null,
    created_at: '2026-08-30T02:00:00',
    ...over,
});

/** Captures the download the page triggers, including the CSV text itself.
 *
 * jsdom's Blob has no `.text()`, so the contents are recorded at construction
 * rather than read back off the object. */
function captureDownload() {
    const csv: string[] = [];
    const RealBlob = globalThis.Blob;
    class RecordingBlob extends RealBlob {
        constructor(parts: BlobPart[], options?: BlobPropertyBag) {
            super(parts, options);
            csv.push(parts.map(String).join(''));
        }
    }
    globalThis.Blob = RecordingBlob as unknown as typeof Blob;

    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });

    const clicked: HTMLAnchorElement[] = [];
    const realClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
        clicked.push(this);
    };

    return {
        csv,
        clicked,
        revokeObjectURL,
        restore: () => {
            HTMLAnchorElement.prototype.click = realClick;
            globalThis.Blob = RealBlob;
        },
    };
}

describe('Valuation CSV export', () => {
    let dl: ReturnType<typeof captureDownload>;

    beforeEach(() => {
        vi.clearAllMocks();
        dl = captureDownload();
        (api.getValuationMacro as any).mockResolvedValue({
            us10y: 4.2, rate_adjustment_factor: 1, source: 'FRED', fallback_used: false,
        });
        (api.getValuationWatchlist as any).mockResolvedValue([]);
    });

    afterEach(() => dl.restore());

    it('writes one CSV row per rendered snapshot, with a header', async () => {
        (api.getValuationSnapshots as any).mockResolvedValue([
            snapshot(),
            snapshot({ id: 2, ticker: 'CSI300', row_kind: 'tracked_index', display_name: '沪深300' }),
        ]);

        render(<Valuation />);

        const button = await screen.findByRole('button', { name: /export/i });
        await waitFor(() => expect(button).not.toBeDisabled());
        fireEvent.click(button);

        await waitFor(() => expect(dl.csv.length).toBe(1));
        const text = dl.csv[0];
        const lines = text.trim().split('\n');

        expect(lines[0]).toContain('Snapshot Date');
        expect(lines[0]).toContain('Ticker');
        expect(lines).toHaveLength(3); // header + 2 rows
        expect(text).toContain('VOO');
        expect(text).toContain('CSI300');

        // Downloaded as a dated .csv, and the object URL is released.
        expect(dl.clicked[0].download).toMatch(/^valuation-\d{4}-\d{2}-\d{2}\.csv$/);
        expect(dl.revokeObjectURL).toHaveBeenCalled();
    });

    it('quotes only values that would otherwise break the row', async () => {
        (api.getValuationSnapshots as any).mockResolvedValue([
            snapshot({ display_name: 'Berkshire Hathaway, Inc.', signal_basis: 'plain' }),
        ]);

        render(<Valuation />);
        const button = await screen.findByRole('button', { name: /export/i });
        await waitFor(() => expect(button).not.toBeDisabled());
        fireEvent.click(button);

        await waitFor(() => expect(dl.csv.length).toBe(1));
        const text = dl.csv[0];

        expect(text).toContain('"Berkshire Hathaway, Inc."'); // comma forces quoting
        expect(text).toContain(',plain,');                    // nothing else gets quoted
    });

    it('is disabled, and exports nothing, when there are no snapshots', async () => {
        (api.getValuationSnapshots as any).mockResolvedValue([]);

        render(<Valuation />);

        const button = await screen.findByRole('button', { name: /export/i });
        await waitFor(() => expect(button).toBeDisabled());
        fireEvent.click(button);

        expect(dl.csv).toHaveLength(0);
    });
});
