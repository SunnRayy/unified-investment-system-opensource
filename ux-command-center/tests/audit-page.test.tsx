import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '../test-utils';
import userEvent from '@testing-library/user-event';
import { Audit } from '../pages/Audit';

const apiMocks = vi.hoisted(() => ({
  getAuditLogs: vi.fn(),
  getAuditSummary: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
  api: {
    getAuditLogs: apiMocks.getAuditLogs,
    getAuditSummary: apiMocks.getAuditSummary,
  },
}));

const createLogs = () => [
  {
    sync_timestamp: '2026-02-14T10:00:00Z',
    source_system: 'Huinsight',
    target_table: 'positions',
    record_key: 'AAPL',
    conflict_type: 'VALUE_MISMATCH',
    source_value: '100',
    target_value: '90',
    resolution: 'SOURCE_WINS',
  },
  {
    sync_timestamp: '2026-02-14T09:00:00Z',
    source_system: 'PIS',
    target_table: 'positions',
    record_key: 'TSLA',
    conflict_type: 'MISSING',
    source_value: '50',
    target_value: '0',
    resolution: 'ADDED',
  },
];

beforeEach(() => {
  apiMocks.getAuditLogs.mockResolvedValue(createLogs());
  apiMocks.getAuditSummary.mockResolvedValue({
    total_logs: 2,
    last_sync_timestamp: '2026-02-14T10:30:00Z',
    unresolved_conflicts: 1,
  });
});

describe('audit page', () => {
  it('renders summary data', async () => {
    render(<Audit />);

    expect(await screen.findByText('Unified Audit & Sync Center')).toBeInTheDocument();
    expect(screen.getByText('Total Logs')).toBeInTheDocument();
    expect(screen.getByTestId('audit-card-total-logs')).toHaveTextContent('2');
  });

  it('filters logs by search term', async () => {
    const user = userEvent.setup();
    render(<Audit />);

    expect(await screen.findByText('AAPL')).toBeInTheDocument();
    expect(screen.getByText('TSLA')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Filter audit assets'), 'AAPL');

    expect(screen.getByText('AAPL')).toBeInTheDocument();
    expect(screen.queryByText('TSLA')).not.toBeInTheDocument();
  });

  it('reloads data when refresh is clicked', async () => {
    const user = userEvent.setup();
    render(<Audit />);

    await screen.findByText('Unified Audit & Sync Center');
    await waitFor(() => {
      expect(apiMocks.getAuditLogs.mock.calls.length).toBeGreaterThan(0);
      expect(apiMocks.getAuditSummary.mock.calls.length).toBeGreaterThan(0);
    });

    const logsCallsBefore = apiMocks.getAuditLogs.mock.calls.length;
    const summaryCallsBefore = apiMocks.getAuditSummary.mock.calls.length;

    await user.click(screen.getByRole('button', { name: /refresh data/i }));

    await waitFor(() => {
      expect(apiMocks.getAuditLogs.mock.calls.length).toBeGreaterThan(logsCallsBefore);
      expect(apiMocks.getAuditSummary.mock.calls.length).toBeGreaterThan(summaryCallsBefore);
    });
  });

  it('uses day card styling in default mode', async () => {
    render(<Audit />);

    const page = await screen.findByTestId('audit-page');
    expect(page.className).toContain('bg-gray-50');

    const card = screen.getByTestId('audit-card-total-logs');
    expect(card.className).toContain('bg-white');
    expect(card.className).toContain('border-gray-200');
  });
});
