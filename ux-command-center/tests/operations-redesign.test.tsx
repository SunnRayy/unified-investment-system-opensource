import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { AssetCaseFile } from '../pages/AssetCaseFile';
import { ImportWorkbench } from '../pages/ImportWorkbench';

const operationsMocks = vi.hoisted(() => ({
  getPortfolioAudit: vi.fn(),
  getAssetClassAudit: vi.fn(),
  getAssetCaseFile: vi.fn(),
  getSyncHistory: vi.fn(),
  getSyncHistoryDetail: vi.fn(),
}));

const managementMocks = vi.hoisted(() => ({
  searchTransactions: vi.fn(),
  getTransactionFilters: vi.fn(),
  previewImports: vi.fn(),
}));

const auditMocks = vi.hoisted(() => ({
  getIntegrity: vi.fn(),
  runOnDemandAudit: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  startSync: vi.fn(),
}));

vi.mock('../src/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/services/api')>();
  return {
    ...actual,
    OperationsAPI: operationsMocks,
    ManagementAPI: managementMocks,
    AuditAPI: auditMocks,
    api: {
      ...actual.api,
      startSync: apiMocks.startSync,
    },
  };
});

describe('operations redesign behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    operationsMocks.getPortfolioAudit.mockResolvedValue({
      last_sync_timestamp: '2026-03-13T09:00:00',
      integrity: { passed: 17, total: 18, all_passed: false },
      open_anomalies: 2,
      reader_warnings: 1,
      legacy_influence_cases: 1,
      global_health: [{ key: 'x', label: 'x', status: 'ok' }],
      asset_classes: [
        {
          class_name: 'US Equity',
          current_value: 231400,
          status: 'warning',
          source_signal_summary: [{ source_system: 'Schwab_CSV', asset_count: 1 }],
          open_case_count: 1,
        },
      ],
      source_strip: [{ source_system: 'Schwab_CSV', flagged_asset_count: 1 }],
    });

        operationsMocks.getAssetCaseFile.mockResolvedValue({
      asset_id: 'US_STK_SGOV',
      display_name: 'SGOV ETF',
      breadcrumb: { portfolio: 'Portfolio', asset_class: 'US Equity', asset: 'US_STK_SGOV' },
      severity: 'high',
      current_state: {
        active_source: 'Schwab_CSV',
        active_shadow_status: false,
        current_quantity: 446,
        current_market_value: 231400,
        last_snapshot_date: '2026-03-08',
      },
      authority_context: {
        expected_authority_source: 'Schwab_CSV',
        competing_sources: ['PIS_SQLite'],
        legacy_influence_flag: true,
        shadow_conflict_flag: false,
      },
      signals: ['Legacy transaction post-dates reader snapshot'],
      source_trace: [
        {
          timestamp: '2026-03-08T09:12:00',
          source_system: 'Schwab_CSV',
          evidence_type: 'transaction',
          description: 'Buy transaction loaded',
        },
        {
          timestamp: '2026-03-08T09:10:00',
          source_system: 'Schwab_CSV',
          evidence_type: 'reader snapshot',
          description: 'Snapshot updated',
        },
        {
          timestamp: '2026-03-08T09:05:00',
          source_system: 'Sync',
          evidence_type: 'audit run',
          description: 'Sync run completed',
        },
      ],
      evidence_counts: { transactions: 2, snapshots: 3, sync_runs: 1 },
      quick_actions: {
        transactions: '/transactions?asset_id=US_STK_SGOV',
        sync_history: '/import?asset_id=US_STK_SGOV',
      },
    });

    operationsMocks.getSyncHistory.mockResolvedValue({
      runs: [
        {
          id: 'run-1',
          timestamp: '2026-03-11T08:13:00',
          type: 'sync',
          net_worth_delta: 0,
          integrity_result: '18/18',
          integrity_status: 'ok',
          blocking_failed: 0,
          warning_count: 1,
          sources_affected: ['Schwab_CSV', 'CN_Fund_Excel'],
          alert: false,
          is_no_change: false,
        },
      ],
    });

    operationsMocks.getSyncHistoryDetail.mockResolvedValue({
      id: 'run-1',
      timestamp: '2026-03-11T08:13:00',
      type: 'sync',
      net_worth_before: 5300000,
      net_worth_after: 5310000,
      net_worth_delta: 0.19,
      integrity_result: '18/18',
      integrity_status: 'ok',
      blocking_failed: 0,
      by_source_before: {},
      by_source_after: {},
      reader_counts: {
        Schwab_CSV: { read: 12, inserted: 12 },
      },
      warnings: ['completed sync', 'mismatch detected'],
      integrity_checks: [{ name: 'source_reconciliation_schwab', passed: true, blocking: false }],
      alert: false,
      is_no_change: false,
      sources_affected: ['Schwab_CSV', 'CN_Fund_Excel'],
    });

            managementMocks.previewImports.mockResolvedValue({
      readers: [
        {
          reader: 'Schwab_CSV',
          status: 'warning',
          holdings_count: 12,
          transactions_count: 32,
          warnings: ['threshold exceeded'],
          new_assets: ['US_STK_NEW'],
          conflicts: ['US_STK_X'],
        },
      ],
    });

            apiMocks.startSync.mockResolvedValue({ status: 'started' });
  });

  it('Asset Case File shows healthy issue summary rows without red error icon', async () => {
    operationsMocks.getAssetCaseFile.mockResolvedValueOnce({
      asset_id: 'US_STK_SGOV',
      display_name: 'SGOV ETF',
      breadcrumb: { portfolio: 'Portfolio', asset_class: 'US Equity', asset: 'US_STK_SGOV' },
      severity: 'low',
      current_state: {
        active_source: 'Schwab_CSV',
        active_shadow_status: false,
        current_quantity: 446,
        current_market_value: 231400,
        last_snapshot_date: '2026-03-08',
      },
      authority_context: {
        expected_authority_source: 'Schwab_CSV',
        competing_sources: [],
        legacy_influence_flag: false,
        shadow_conflict_flag: false,
      },
      signals: ['No anomalies detected — asset appears healthy'],
      source_trace: [],
      evidence_counts: { transactions: 2, snapshots: 3, sync_runs: 1 },
      quick_actions: {
        transactions: '/transactions?asset_id=US_STK_SGOV',
        sync_history: '/import?asset_id=US_STK_SGOV',
      },
    });

    render(
      <MemoryRouter initialEntries={['/asset-case-file?asset_id=US_STK_SGOV']}>
        <Routes>
          <Route path="/asset-case-file" element={<AssetCaseFile />} />
        </Routes>
      </MemoryRouter>
    );

    const healthySignal = await screen.findByText('No anomalies detected — asset appears healthy');
    const signalRow = healthySignal.closest('li');
    expect(signalRow).not.toBeNull();
    expect(within(signalRow as HTMLElement).getByText('check_circle')).toBeInTheDocument();
  });

  it('Sync / Import History wires sync action and removes fake scheduler card', async () => {
    const user = userEvent.setup();
    render(<ImportWorkbench />);

    expect(await screen.findByText('Sync / Import History')).toBeInTheDocument();
    expect(screen.queryByText('Date Range')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /refresh runs/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument();
    expect(screen.queryByText('Automated Sync')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /sync now/i }));
    expect(screen.queryByText(/refreshing run history/i)).not.toBeInTheDocument();
    await waitFor(() => expect(apiMocks.startSync).toHaveBeenCalledTimes(1));
  });

  it('Sync / Import History keeps run details readable with collapsed warning log by default', async () => {
    const user = userEvent.setup();
    operationsMocks.getSyncHistoryDetail.mockResolvedValueOnce({
      id: 'run-compact',
      timestamp: '2026-03-11T08:13:00',
      type: 'sync',
      net_worth_before: 5300000,
      net_worth_after: 5310000,
      net_worth_delta: 0.19,
      integrity_result: '18/18',
      integrity_status: 'ok',
      blocking_failed: 0,
      by_source_before: {},
      by_source_after: {},
      reader_counts: {
        transactions_synced: { read: 12, inserted: 12 },
      },
      warnings: [
        'w1 Backup created',
        'w2 Cleanup metadata',
        'w3 Normalized classes',
        'w4 Threshold exceeded',
        'w5 Reader mismatch',
        'w6 Completed sync',
      ],
      integrity_checks: [{ name: 'source_reconciliation_schwab', passed: true, blocking: false }],
      alert: true,
      is_no_change: false,
      sources_affected: ['Schwab_CSV'],
    });

    render(<ImportWorkbench />);
    expect(await screen.findByText('Sync / Import History')).toBeInTheDocument();
    expect(await screen.findByText('Warning Log (6)')).toBeInTheDocument();
    expect(screen.getByText(/Showing 4 of 6 entries/i)).toBeInTheDocument();
    expect(screen.queryByText('w5 Reader mismatch')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show all 6/i }));
    expect(await screen.findByText('w6 Completed sync')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show less/i }));
    expect(screen.queryByText('w6 Completed sync')).not.toBeInTheDocument();
  });

  it('Sync / Import History balances table columns and warning log typography', async () => {
    render(<ImportWorkbench />);
    expect(await screen.findByText('Sync / Import History')).toBeInTheDocument();

    const runsHeader = screen.getByRole('columnheader', { name: 'Date & Time' });
    const runsTable = runsHeader.closest('table');
    expect(runsTable).not.toBeNull();
    const layoutSection = runsTable?.closest('section');
    expect(layoutSection).toHaveClass('xl:grid-cols-[minmax(0,1fr)_560px]');

    expect(screen.getByRole('columnheader', { name: 'Net Worth Delta' })).toHaveClass('w-[1%]');
    expect(screen.getByRole('columnheader', { name: 'Integrity' })).toHaveClass('w-[1%]');
    expect(screen.getByRole('columnheader', { name: 'Date & Time' })).toHaveClass('w-[32%]');

    const warningHeading = screen.getByText(/Warning Log \(/i);
    expect(warningHeading).toHaveClass('text-base');
    expect(warningHeading).toHaveClass('font-semibold');
    expect(warningHeading).toHaveClass('text-gray-800');
    expect(screen.getByText('completed sync')).toHaveClass('text-[13px]');
  });

  it('Integrity column renders amber for degraded (advisory-only) and red for failed (blocking)', async () => {
    operationsMocks.getSyncHistory.mockResolvedValue({
      runs: [
        {
          id: 'run-degraded',
          timestamp: '2026-05-01T10:00:00',
          type: 'sync',
          net_worth_delta: 0.05,
          integrity_result: '13/14',
          integrity_status: 'degraded',
          blocking_failed: 0,
          warning_count: 1,
          sources_affected: ['Schwab_CSV'],
          alert: false,
          is_no_change: false,
        },
        {
          id: 'run-failed',
          timestamp: '2026-05-02T10:00:00',
          type: 'sync',
          net_worth_delta: -0.5,
          integrity_result: '9/14',
          integrity_status: 'failed',
          blocking_failed: 2,
          warning_count: 3,
          sources_affected: ['CN_Fund_Excel'],
          alert: true,
          is_no_change: false,
        },
      ],
    });

    render(<ImportWorkbench />);
    expect(await screen.findByText('Sync / Import History')).toBeInTheDocument();

    // Degraded run: shows "(advisory)" label and amber-colored score text.
    // The outer <span> contains both the score text node and the child "(advisory)" span,
    // so its textContent is "13/14(advisory)" — walk up from the label to reach the styled parent.
    const advisoryLabel = screen.getByText('(advisory)');
    expect(advisoryLabel).toBeInTheDocument();
    const degradedScoreSpan = advisoryLabel.parentElement;
    expect(degradedScoreSpan).toHaveClass('text-amber-600');

    // Failed run: score text is a standalone text node — exact match works.
    const failedScoreSpan = screen.getByText('9/14');
    expect(failedScoreSpan).toHaveClass('text-red-600');
  });

  it('Detail panel shows amber (advisory) for degraded integrity status', async () => {
    operationsMocks.getSyncHistory.mockResolvedValue({
      runs: [
        {
          id: 'run-degraded',
          timestamp: '2026-05-01T10:00:00',
          type: 'sync',
          net_worth_delta: 0.05,
          integrity_result: '13/14',
          integrity_status: 'degraded',
          blocking_failed: 0,
          warning_count: 1,
          sources_affected: ['Schwab_CSV'],
          alert: false,
          is_no_change: false,
        },
      ],
    });

    operationsMocks.getSyncHistoryDetail.mockResolvedValueOnce({
      id: 'run-degraded',
      timestamp: '2026-05-01T10:00:00',
      type: 'sync',
      net_worth_before: 5300000,
      net_worth_after: 5310000,
      net_worth_delta: 0.05,
      integrity_result: '13/14',
      integrity_status: 'degraded',
      blocking_failed: 0,
      by_source_before: {},
      by_source_after: {},
      reader_counts: {},
      warnings: [],
      integrity_checks: [{ name: 'trade_log_verdict_consistency', passed: false, blocking: false }],
      alert: false,
      is_no_change: false,
      sources_affected: ['Schwab_CSV'],
    });

    render(<ImportWorkbench />);
    expect(await screen.findByText('Sync / Import History')).toBeInTheDocument();

    // Wait for the detail panel to load (auto-selects first run on mount).
    // Both the list view and detail panel render (advisory) when status is degraded.
    await waitFor(() => {
      const advisoryLabels = screen.getAllByText('(advisory)');
      expect(advisoryLabels.length).toBeGreaterThanOrEqual(1);
    });

    // The (advisory) label in the detail panel is a child span inside the amber-600 value span.
    // Walk up the DOM tree to find the nearest ancestor span that carries text-amber-600.
    const advisoryLabels = screen.getAllByText('(advisory)');
    const hasAmberAncestor = advisoryLabels.some((el) => {
      let node: Element | null = el.parentElement;
      while (node) {
        if (node.tagName === 'SPAN' && node.className.includes('text-amber-600')) return true;
        node = node.parentElement;
      }
      return false;
    });
    expect(hasAmberAncestor).toBe(true);
  });
});
