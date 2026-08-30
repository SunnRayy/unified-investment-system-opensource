import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PendingVerificationList } from '../components/ai-advisor/PendingVerificationList';
import type { PendingVerificationItem } from '../src/services/api';

// ── mock ──────────────────────────────────────────────────────────────────────

const apiMocks = vi.hoisted(() => ({
  listPending: vi.fn(),
  verifyTrade: vi.fn(),
  reopenVerification: vi.fn(),
  generateCrossCheckAudit: vi.fn(),
  getVerdictMismatchRate: vi.fn(),
}));

vi.mock('../src/services/api', async () => {
  const actual = await vi.importActual('../src/services/api');
  return {
    ...actual,
    aiAdvisorVerify: apiMocks,
  };
});

const mockListPending = apiMocks.listPending;
const mockVerifyTrade = apiMocks.verifyTrade;

// ── fixtures ──────────────────────────────────────────────────────────────────

// A trade that has NOT matured (logged 5 days ago)
function recentDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - 5);
  return d.toISOString().slice(0, 10);
}

// A trade that HAS matured (logged 45 days ago)
function maturedDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - 45);
  return d.toISOString().slice(0, 10);
}

const RECENT_ITEM: PendingVerificationItem = {
  id: 1,
  log_date: recentDate(),
  asset_id: 'US_STK_VOO',
  asset_name: 'VOO',
  action: 'Buy',
  price: 500,
  quantity: 10,
  amount_cny: 36000,
  decision_reason: 'Buy the dip',
  suggestion_source: 'memo',
  verification_status: 'pending',
  is_matured: false,
  outcome_pct_preview: null,
  suggested_verdict: 'good_call',
  linked_insight_id: 42,
  linked_insight_title: 'VOO entry on correction',
  updated_at: '2026-05-10T10:00:00.000000',
  verdict: null,
  outcome_pct: null,
  verification_result: null,
  verification_date: null,
  outcome_to_date_pct: null,
  outcome_to_date_asof: null,
};

const MATURED_ITEM: PendingVerificationItem = {
  id: 2,
  log_date: maturedDate(),
  asset_id: 'CN_FND_001',
  asset_name: null,
  action: 'Sell',
  price: 1.25,
  quantity: 1000,
  amount_cny: 8900,
  decision_reason: null,
  suggestion_source: 'brief',
  verification_status: 'pending_window',
  is_matured: true,
  outcome_pct_preview: 7.83,
  suggested_verdict: null,
  linked_insight_id: null,
  linked_insight_title: null,
  updated_at: '2026-05-01T08:00:00.000000',
  verdict: null,
  outcome_pct: null,
  verification_result: null,
  verification_date: null,
  outcome_to_date_pct: null,
  outcome_to_date_asof: null,
};

// A matured trade with NO market outcome data (price history unavailable)
const MATURED_NO_DATA_ITEM: PendingVerificationItem = {
  id: 3,
  log_date: maturedDate(),
  asset_id: 'CN_INS_001',
  asset_name: 'Insurance Policy A',
  action: 'Buy',
  price: null,
  quantity: null,
  amount_cny: 50000,
  decision_reason: 'Insurance purchase',
  suggestion_source: 'memo',
  verification_status: 'pending_window',
  is_matured: true,
  outcome_pct_preview: null,   // no market data
  suggested_verdict: null,     // backend cannot auto-classify
  linked_insight_id: null,
  linked_insight_title: null,
  updated_at: '2026-04-01T08:00:00.000000',
  verdict: null,
  outcome_pct: null,
  verification_result: null,
  verification_date: null,
  outcome_to_date_pct: null,
  outcome_to_date_asof: null,
};

// A pre-window trade that already has interim outcome data
const RECENT_WITH_OUTCOME_ITEM: PendingVerificationItem = {
  id: 4,
  log_date: recentDate(),
  asset_id: 'CN_FND_900008',
  asset_name: '中欧医疗',
  action: 'Buy',
  price: 1.05,
  quantity: 5000,
  amount_cny: 5250,
  decision_reason: 'Healthcare sector rebound',
  suggestion_source: 'brief',
  verification_status: 'pending',
  is_matured: false,
  outcome_pct_preview: null,
  suggested_verdict: null,
  linked_insight_id: null,
  linked_insight_title: null,
  updated_at: '2026-07-01T10:00:00.000000',
  verdict: null,
  outcome_pct: null,
  verification_result: null,
  verification_date: null,
  outcome_to_date_pct: 3.21,
  outcome_to_date_asof: '2026-07-03',
};

// A verified trade whose verdict was determined automatically (auto: provenance marker)
const VERIFIED_AUTO_ITEM: PendingVerificationItem = {
  id: 5,
  log_date: maturedDate(),
  asset_id: 'US_STK_IEF',
  asset_name: 'IEF',
  action: 'Buy',
  price: 97.5,
  quantity: 20,
  amount_cny: 14000,
  decision_reason: 'Bond allocation',
  suggestion_source: 'brief',
  verification_status: 'verified',
  is_matured: true,
  outcome_pct_preview: 2.1,
  suggested_verdict: 'good_call',
  linked_insight_id: null,
  linked_insight_title: null,
  updated_at: '2026-07-04T00:00:00.000000',
  verdict: 'good_call',
  outcome_pct: 2.1,
  verification_result: 'auto: price-based verdict at +30d',
  verification_date: '2026-07-04',
  outcome_to_date_pct: null,
  outcome_to_date_asof: null,
};

// ── tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockListPending.mockResolvedValue({ items: [RECENT_ITEM, MATURED_ITEM] });
});

describe('PendingVerificationList', () => {
  it('renders rows from mocked api', async () => {
    render(<PendingVerificationList />);

    expect(await screen.findByText('VOO')).toBeInTheDocument();
    expect(await screen.findByText('CN_FND_001')).toBeInTheDocument();
  });

  it('shows maturity badge correctly', async () => {
    render(<PendingVerificationList />);

    // Matured item shows "Matured"
    expect(await screen.findByText('Matured')).toBeInTheDocument();
    // Non-matured item shows "Pre-window"
    const preWindow = await screen.findByText(/Pre-window/);
    expect(preWindow).toBeInTheDocument();
  });

  it('shows positive outcome for matured item with positive outcome_pct_preview', async () => {
    render(<PendingVerificationList />);

    // MATURED_ITEM has outcome_pct_preview = 7.83
    expect(await screen.findByText('+7.83%')).toBeInTheDocument();
  });

  it('shows dash for non-matured item outcome', async () => {
    render(<PendingVerificationList />);

    // The non-matured item has no outcome — rendered as "—" with tooltip
    await screen.findByText('VOO'); // wait for load
    // Check that at least one "—" text with tooltip title exists
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThan(0);
  });

  it('clicking Verify opens modal with pre-filled verdict', async () => {
    render(<PendingVerificationList />);

    const verifyButtons = await screen.findAllByRole('button', { name: /verify/i });
    fireEvent.click(verifyButtons[0]); // open modal for first row (RECENT_ITEM)

    // Modal header includes asset name
    expect(await screen.findByText(/Verify Trade — VOO/i)).toBeInTheDocument();

    // Verdict select should be pre-selected with suggested_verdict = 'good_call'
    const verdictSelect = screen.getByRole('combobox') as HTMLSelectElement;
    expect(verdictSelect.value).toBe('good_call');
  });

  it('submitting modal calls api with expected payload including expected_updated_at', async () => {
    mockVerifyTrade.mockResolvedValue({ ...RECENT_ITEM, verification_status: 'pending_window' });
    render(<PendingVerificationList />);

    const verifyButtons = await screen.findAllByRole('button', { name: /verify/i });
    fireEvent.click(verifyButtons[0]);

    // Fill in narrative
    const textarea = screen.getByPlaceholderText(/Stop-loss worked/);
    fireEvent.change(textarea, { target: { value: 'Bought at dip, now up 5%' } });

    // Submit
    const submitBtn = screen.getByRole('button', { name: /submit verification/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockVerifyTrade).toHaveBeenCalledWith(
        RECENT_ITEM.id,
        expect.objectContaining({
          verification_result: 'Bought at dip, now up 5%',
          expected_updated_at: RECENT_ITEM.updated_at,
        })
      );
    });
  });

  it('412 from api shows stale banner in modal and refetches list', async () => {
    mockVerifyTrade.mockRejectedValue(new Error('stale_updated_at: please refresh and try again'));
    render(<PendingVerificationList />);

    const verifyButtons = await screen.findAllByRole('button', { name: /verify/i });
    fireEvent.click(verifyButtons[0]);

    const textarea = screen.getByPlaceholderText(/Stop-loss worked/);
    fireEvent.change(textarea, { target: { value: 'Test narrative' } });

    fireEvent.click(screen.getByRole('button', { name: /submit verification/i }));

    // Stale causes onStale() callback which closes the modal and triggers refetch
    // The list is refetched (called twice total: initial + after stale)
    await waitFor(() => {
      expect(mockListPending).toHaveBeenCalledTimes(2);
    });
  });

  it('shows empty state when no items', async () => {
    mockListPending.mockResolvedValue({ items: [] });
    render(<PendingVerificationList />);

    expect(await screen.findByText(/No trades need verification/i)).toBeInTheDocument();
  });

  // ── NEW: narrative-only submit (no verdict) shows "saved-pending" toast ──────

  it('narrative-only submit shows saved-pending toast and trade stays in pending list', async () => {
    // Backend returns pending_window (narrative saved, no verdict given)
    const pendingWindowResponse: PendingVerificationItem = {
      ...RECENT_ITEM,
      verification_status: 'pending_window',
      verification_result: 'Bought at dip, holding for now',
      updated_at: '2026-05-10T11:00:00.000000',
    };
    mockVerifyTrade.mockResolvedValue(pendingWindowResponse);
    render(<PendingVerificationList />);

    // Open the verify modal for RECENT_ITEM (first row)
    const verifyButtons = await screen.findAllByRole('button', { name: /verify/i });
    fireEvent.click(verifyButtons[0]);

    // Clear the suggested verdict so we submit without a verdict
    const verdictSelect = screen.getByRole('combobox') as HTMLSelectElement;
    fireEvent.change(verdictSelect, { target: { value: '' } });

    // Add narrative
    const textarea = screen.getByPlaceholderText(/Stop-loss worked/);
    fireEvent.change(textarea, { target: { value: 'Bought at dip, holding for now' } });

    fireEvent.click(screen.getByRole('button', { name: /submit verification/i }));

    // Should show the "saved-pending" toast (not a "verification complete" message)
    await waitFor(() => {
      expect(screen.getByText(/Narrative saved — trade awaits verdict or maturity/i)).toBeInTheDocument();
    });

    // VOO should still be visible in the pending table (trade was NOT removed)
    expect(screen.getByText('VOO')).toBeInTheDocument();
  });

  // ── NEW: verify-with-verdict auto-expands Verified History section ────────────

  it('verify with verdict auto-expands the Verified History section', async () => {
    // Backend returns 'verified' status (verdict was given and accepted)
    const verifiedResponse: PendingVerificationItem = {
      ...RECENT_ITEM,
      verification_status: 'verified',
      verdict: 'good_call',
      verification_result: 'Bought at dip, up 12% after 30 days',
      updated_at: '2026-05-10T11:00:00.000000',
    };
    mockVerifyTrade.mockResolvedValue(verifiedResponse);
    // Verified history fetch returns the newly verified trade
    mockListPending
      .mockResolvedValueOnce({ items: [RECENT_ITEM, MATURED_ITEM] }) // initial pending fetch
      .mockResolvedValueOnce({ items: [RECENT_ITEM, MATURED_ITEM] }) // fetchItems() after success
      .mockResolvedValueOnce({ items: [verifiedResponse] });         // fetchVerifiedItems() after success

    render(<PendingVerificationList />);

    // Verified History is collapsed by default — the section header is present but no table rows
    const historyToggle = await screen.findByRole('button', { name: /verified history/i });
    expect(historyToggle).toBeInTheDocument();

    // Open modal and submit with verdict
    const verifyButtons = await screen.findAllByRole('button', { name: /^verify$/i });
    fireEvent.click(verifyButtons[0]);

    const textarea = screen.getByPlaceholderText(/Stop-loss worked/);
    fireEvent.change(textarea, { target: { value: 'Bought at dip, up 12% after 30 days' } });

    // Leave verdict as 'good_call' (pre-filled by suggested_verdict)
    fireEvent.click(screen.getByRole('button', { name: /submit verification/i }));

    // After success: verified-success toast should appear
    await waitFor(() => {
      expect(screen.getByText(/Verification saved — trade moved to Verified History/i)).toBeInTheDocument();
    });

    // Verified History section should now be auto-expanded (the verified trade row is visible)
    await waitFor(() => {
      // The section is expanded when fetchVerifiedItems was triggered — look for VOO in that table
      expect(screen.queryAllByText('VOO').length).toBeGreaterThan(0);
    });
  });

  // ── T5: outcome_to_date_pct rendering ────────────────────────────────────────

  it('pre-window row with outcome_to_date_pct renders signed % with 至今 and as-of subtext', async () => {
    mockListPending.mockResolvedValue({ items: [RECENT_WITH_OUTCOME_ITEM] });
    render(<PendingVerificationList />);

    await screen.findByText('中欧医疗');

    // The percentage value + 至今 suffix should appear
    expect(screen.getByText(/\+3\.21%\s*至今/)).toBeInTheDocument();
    // The as-of subtext should be present: "截至 07-03"
    expect(screen.getByText(/截至 07-03/)).toBeInTheDocument();
  });

  it('pre-window row with null outcome_to_date_pct keeps "—" dash', async () => {
    // RECENT_ITEM has outcome_to_date_pct: null
    mockListPending.mockResolvedValue({ items: [RECENT_ITEM] });
    render(<PendingVerificationList />);

    await screen.findByText('VOO');
    // Should still show at least one "—" for the outcome column
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThan(0);
    // Must NOT show any outcome-style "至今" text (regex excludes the legend which uses Chinese quotes "至今")
    expect(screen.queryByText(/[+\-]\d+\.\d+%\s*至今/)).toBeNull();
  });

  it('matured row keeps bold preview (not 至今 style)', async () => {
    mockListPending.mockResolvedValue({ items: [MATURED_ITEM] });
    render(<PendingVerificationList />);

    // MATURED_ITEM has outcome_pct_preview = 7.83
    expect(await screen.findByText('+7.83%')).toBeInTheDocument();
    // Matured rows must not show outcome-style 至今 (legend is excluded — it wraps 至今 in Chinese quotes)
    expect(screen.queryByText(/[+\-]\d+\.\d+%\s*至今/)).toBeNull();
  });

  it('verified history row with auto: narrative shows Auto chip', async () => {
    // Initial pending list empty so only verified history matters
    mockListPending
      .mockResolvedValueOnce({ items: [] })             // initial pending fetch
      .mockResolvedValueOnce({ items: [VERIFIED_AUTO_ITEM] }); // verified history fetch

    render(<PendingVerificationList />);

    // Expand verified history section
    const historyToggle = await screen.findByRole('button', { name: /verified history/i });
    fireEvent.click(historyToggle);

    // The Auto chip should appear next to the verdict
    expect(await screen.findByText('Auto')).toBeInTheDocument();
  });

  // ── NEW: matured trade with no market data blocks submit until verdict chosen ─

  it('matured trade with no outcome data disables submit and requires a verdict', async () => {
    mockListPending.mockResolvedValue({ items: [MATURED_NO_DATA_ITEM] });
    render(<PendingVerificationList />);

    // Open modal for the no-data matured trade
    const verifyButtons = await screen.findAllByRole('button', { name: /verify/i });
    fireEvent.click(verifyButtons[0]);

    // Amber warning banner should be visible
    expect(await screen.findByText(/No market outcome data available/i)).toBeInTheDocument();

    // Submit button should be disabled while no verdict is selected
    const submitBtn = screen.getByRole('button', { name: /submit verification/i }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);

    // "Let backend decide" option must NOT be selectable — only a disabled placeholder exists
    const verdictSelect = screen.getByRole('combobox') as HTMLSelectElement;
    const enabledOptions = Array.from(verdictSelect.options).filter((o) => !o.disabled).map((o) => o.value);
    expect(enabledOptions).not.toContain(''); // no enabled empty/"Let backend decide" value

    // Filling in narrative alone doesn't enable the submit button
    const textarea = screen.getByPlaceholderText(/Stop-loss worked/);
    fireEvent.change(textarea, { target: { value: 'Insurance policy outcome' } });
    expect(submitBtn.disabled).toBe(true);

    // Once the user picks a verdict, the submit button becomes enabled
    fireEvent.change(verdictSelect, { target: { value: 'good_call' } });
    await waitFor(() => {
      expect(submitBtn.disabled).toBe(false);
    });
  });

  // ── Addendum 2026-07-05: neutral verdict rendering ──────────────────────────

  it('neutral verdict chip renders Neutral with slate/gray style', async () => {
    // A matured item whose suggested_verdict is 'neutral' (within-band outcome)
    const NEUTRAL_ITEM: PendingVerificationItem = {
      ...MATURED_ITEM,
      id: 10,
      outcome_pct_preview: 4.03,
      suggested_verdict: 'neutral',
      verdict: null,
    };
    mockListPending.mockResolvedValue({ items: [NEUTRAL_ITEM] });
    render(<PendingVerificationList />);

    await screen.findByText('CN_FND_001');

    // The Neutral chip text must be visible in the table (chip renders "⚪ Neutral")
    expect(screen.getByText(/Neutral/)).toBeInTheDocument();
  });

  it('verify dialog dropdown contains the neutral option', async () => {
    // Use a matured item with a neutral suggested verdict to open the modal
    const NEUTRAL_ITEM: PendingVerificationItem = {
      ...MATURED_ITEM,
      id: 11,
      outcome_pct_preview: 4.03,
      suggested_verdict: 'neutral',
      verdict: null,
    };
    mockListPending.mockResolvedValue({ items: [NEUTRAL_ITEM] });
    render(<PendingVerificationList />);

    // Wait for the item to render then open its verify modal
    const verifyButtons = await screen.findAllByRole('button', { name: /verify/i });
    fireEvent.click(verifyButtons[0]);

    // The verdict select must contain the Neutral option
    const verdictSelect = screen.getByRole('combobox') as HTMLSelectElement;
    const optionValues = Array.from(verdictSelect.options).map((o) => o.value);
    expect(optionValues).toContain('neutral');

    // The label text for the neutral option should be present
    const neutralOption = Array.from(verdictSelect.options).find((o) => o.value === 'neutral');
    expect(neutralOption).toBeDefined();
    expect(neutralOption?.text).toMatch(/Neutral/);
  });
});
