import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CrossCheckAudit } from '../components/ai-advisor/CrossCheckAudit';
import type { CrossCheckAuditResult } from '../src/services/api';

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

const mockGenerateCrossCheckAudit = apiMocks.generateCrossCheckAudit;

// ── fixtures ──────────────────────────────────────────────────────────────────

const AUDIT_RESULT: CrossCheckAuditResult = {
  audit_markdown: '## Cross-check Summary\n\nAll 3 adopted insights were **executed on time**.\n\n- SGOV position closed correctly\n- VOO bought at target price',
  summary: { total_insights: 3, adopted: 3, scored: 2 },
  model_used: 'gemini-1.5-pro',
  generated_at: '2026-05-23T15:00:00.000000',
  report_id: 101,
};

// ── tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockGenerateCrossCheckAudit.mockResolvedValue(AUDIT_RESULT);
});

describe('CrossCheckAudit', () => {
  it('shows generate button when idle', async () => {
    render(<CrossCheckAudit />);

    const btn = screen.getByRole('button', { name: /generate audit/i });
    expect(btn).toBeInTheDocument();
    expect(btn).not.toBeDisabled();
  });

  it('clicking Generate calls api with current period', async () => {
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));

    await waitFor(() => {
      expect(mockGenerateCrossCheckAudit).toHaveBeenCalledTimes(1);
    });

    const [callStart, callEnd] = mockGenerateCrossCheckAudit.mock.calls[0] as [string, string];
    // Should be ~90d period (preset default)
    expect(callStart).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(callEnd).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // callEnd should be today
    const today = new Date().toISOString().slice(0, 10);
    expect(callEnd).toBe(today);
  });

  it('rendered markdown appears after success', async () => {
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));

    // Heading from audit_markdown
    expect(await screen.findByText('Cross-check Summary')).toBeInTheDocument();
    // Bullet item
    expect(await screen.findByText(/SGOV position closed correctly/)).toBeInTheDocument();
    // Bold inline
    expect(await screen.findByText(/executed on time/i)).toBeInTheDocument();
  });

  it('shows model and generated_at footer after success', async () => {
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));

    expect(await screen.findByText(/gemini-1\.5-pro/)).toBeInTheDocument();
  });

  it('shows Regenerate button after first successful audit', async () => {
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));

    expect(await screen.findByRole('button', { name: /regenerate/i })).toBeInTheDocument();
  });

  it('regenerate triggers new api call', async () => {
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));
    await screen.findByRole('button', { name: /regenerate/i });

    fireEvent.click(screen.getByRole('button', { name: /regenerate/i }));

    await waitFor(() => {
      expect(mockGenerateCrossCheckAudit).toHaveBeenCalledTimes(2);
    });
  });

  it('422 shows period-too-large banner', async () => {
    mockGenerateCrossCheckAudit.mockRejectedValue(
      new Error('Period exceeds caps — narrow the date range')
    );
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));

    expect(
      await screen.findByText(/Period exceeds caps/i)
    ).toBeInTheDocument();
  });

  it('generic error shows error banner', async () => {
    mockGenerateCrossCheckAudit.mockRejectedValue(new Error('LLM API timeout'));
    render(<CrossCheckAudit />);

    fireEvent.click(screen.getByRole('button', { name: /generate audit/i }));

    expect(await screen.findByText(/LLM API timeout/i)).toBeInTheDocument();
  });

  it('button is disabled while loading', async () => {
    // Make api hang
    mockGenerateCrossCheckAudit.mockReturnValue(new Promise(() => {}));
    render(<CrossCheckAudit />);

    const btn = screen.getByRole('button', { name: /generate audit/i });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /generating audit/i })).toBeDisabled();
    });
  });
});
