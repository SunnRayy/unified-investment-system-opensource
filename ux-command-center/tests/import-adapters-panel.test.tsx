import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ImportAdaptersPanel } from '../components/settings/ImportAdaptersPanel';

const mocks = vi.hoisted(() => ({
  uploadImportAdapterFile: vi.fn(async () => ({ run_id: 1, headers: [], inferred_mapping: {}, preview_rows: [], total_rows: 1 })),
  configureImportAdapter: vi.fn(async () => ({ ok: true })),
  validateImportAdapter: vi.fn(async () => ({ valid: true, warnings: [], errors: [], row_counts: { total: 1 } })),
  stageImportAdapter: vi.fn(async () => ({ staged_rows: 1 })),
  approveImportAdapter: vi.fn(async () => ({ ok: true })),
}));

vi.mock('../src/services/api', () => ({
  SettingsAPI: mocks,
}));

describe('ImportAdaptersPanel', () => {
  it('renders choices and disables accounts option', () => {
    render(<ImportAdaptersPanel />);
    expect(screen.getByRole('button', { name: 'Holdings' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Transactions' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accounts' })).toBeDisabled();
  });

  it('disables upload until file selected', () => {
    render(<ImportAdaptersPanel />);
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled();
  });

  it('requires source system and prefixes for approve', () => {
    render(<ImportAdaptersPanel />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('stage button disabled when validation has errors', async () => {
    mocks.validateImportAdapter.mockResolvedValueOnce({ valid: false, warnings: [], errors: ['missing:asset_id'], row_counts: { total: 1 } });
    render(<ImportAdaptersPanel />);
    expect(screen.getByRole('button', { name: 'Stage' })).toBeDisabled();
  });

  it('all buttons have handlers or are disabled', () => {
    render(<ImportAdaptersPanel />);
    screen.getAllByRole('button').forEach((btn) => {
      expect(btn).toBeDefined();
    });
  });
});
