import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { InvestorProfileEditor } from '../components/settings/InvestorProfileEditor';

const mocks = vi.hoisted(() => ({
  getProfile: vi.fn(),
  updateProfile: vi.fn(),
}));

vi.mock('../src/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/services/api')>();
  return {
    ...actual,
    SettingsAPI: {
      ...actual.SettingsAPI,
      getProfile: mocks.getProfile,
      updateProfile: mocks.updateProfile,
    },
  };
});

const MOCK_PROFILE = {
  display_name: 'Ray',
  avatar_url: null,
  philosophy: {
    goal: 'Long-term wealth compounding',
    horizon: '15 years',
    risk_tolerance: 'Can handle -30% drawdown',
    core_weakness: 'Recency bias',
    portfolio_structure: 'US equities 50%, bonds 20%, alts 30%',
  },
};

describe('InvestorProfileEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', () => {
    // Make getProfile a never-resolving promise so loading state is visible
    mocks.getProfile.mockReturnValue(new Promise(() => {}));
    render(<InvestorProfileEditor />);
    expect(screen.getByText(/loading investor profile/i)).toBeInTheDocument();
  });

  it('populates all five fields from GET /settings/profile on mount', async () => {
    mocks.getProfile.mockResolvedValue(MOCK_PROFILE);
    render(<InvestorProfileEditor />);

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.queryByText(/loading investor profile/i)).not.toBeInTheDocument();
    });

    // Check all five fields are populated
    expect(screen.getByDisplayValue('Long-term wealth compounding')).toBeInTheDocument();
    expect(screen.getByDisplayValue('15 years')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Can handle -30% drawdown')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Recency bias')).toBeInTheDocument();
    expect(screen.getByDisplayValue('US equities 50%, bonds 20%, alts 30%')).toBeInTheDocument();
  });

  it('renders section heading and all five labeled fields', async () => {
    mocks.getProfile.mockResolvedValue(MOCK_PROFILE);
    render(<InvestorProfileEditor />);

    await waitFor(() => {
      expect(screen.queryByText(/loading investor profile/i)).not.toBeInTheDocument();
    });

    // Section heading
    expect(screen.getByText(/Investor Profile/)).toBeInTheDocument();

    // Field labels (checking Chinese + English label presence)
    expect(screen.getByText(/目标 \/ Goal/)).toBeInTheDocument();
    expect(screen.getByText(/期限 \/ Horizon/)).toBeInTheDocument();
    expect(screen.getByText(/风险承受 \/ Risk Tolerance/)).toBeInTheDocument();
    expect(screen.getByText(/核心弱点 \/ Core Weakness/)).toBeInTheDocument();
    expect(screen.getByText(/配置逻辑 \/ Portfolio Structure/)).toBeInTheDocument();
  });

  it('calls updateProfile with { philosophy: {...} } body only when Save is clicked', async () => {
    mocks.getProfile.mockResolvedValue(MOCK_PROFILE);
    mocks.updateProfile.mockResolvedValue(MOCK_PROFILE);

    const user = userEvent.setup();
    render(<InvestorProfileEditor />);

    await waitFor(() => {
      expect(screen.queryByText(/loading investor profile/i)).not.toBeInTheDocument();
    });

    // Edit the goal field to make the form dirty
    const goalTextarea = screen.getByDisplayValue('Long-term wealth compounding');
    await user.clear(goalTextarea);
    await user.type(goalTextarea, 'Updated goal text');

    // Save button should now be enabled
    const saveBtn = screen.getByRole('button', { name: /save profile/i });
    expect(saveBtn).not.toBeDisabled();
    await user.click(saveBtn);

    await waitFor(() => {
      expect(mocks.updateProfile).toHaveBeenCalledTimes(1);
    });

    // Verify the body sent to updateProfile contains ONLY philosophy (no display_name/avatar_url)
    const callArg = mocks.updateProfile.mock.calls[0][0];
    expect(callArg).toHaveProperty('philosophy');
    expect(callArg).not.toHaveProperty('display_name');
    expect(callArg).not.toHaveProperty('avatar_url');
    expect(callArg.philosophy.goal).toBe('Updated goal text');
    expect(callArg.philosophy.horizon).toBe('15 years');
    expect(callArg.philosophy.risk_tolerance).toBe('Can handle -30% drawdown');
    expect(callArg.philosophy.core_weakness).toBe('Recency bias');
    expect(callArg.philosophy.portfolio_structure).toBe('US equities 50%, bonds 20%, alts 30%');
  });

  it('Save button is disabled when form is pristine', async () => {
    mocks.getProfile.mockResolvedValue(MOCK_PROFILE);
    render(<InvestorProfileEditor />);

    await waitFor(() => {
      expect(screen.queryByText(/loading investor profile/i)).not.toBeInTheDocument();
    });

    const saveBtn = screen.getByRole('button', { name: /save profile/i });
    expect(saveBtn).toBeDisabled();
  });

  it('shows save-success message after successful save', async () => {
    mocks.getProfile.mockResolvedValue(MOCK_PROFILE);
    mocks.updateProfile.mockResolvedValue({
      ...MOCK_PROFILE,
      philosophy: { ...MOCK_PROFILE.philosophy, goal: 'Updated goal text' },
    });

    const user = userEvent.setup();
    render(<InvestorProfileEditor />);

    await waitFor(() => {
      expect(screen.queryByText(/loading investor profile/i)).not.toBeInTheDocument();
    });

    const goalTextarea = screen.getByDisplayValue('Long-term wealth compounding');
    await user.clear(goalTextarea);
    await user.type(goalTextarea, 'Updated goal text');
    await user.click(screen.getByRole('button', { name: /save profile/i }));

    await waitFor(() => {
      expect(screen.getByText(/profile saved/i)).toBeInTheDocument();
    });
  });

  it('shows error message when getProfile fails', async () => {
    mocks.getProfile.mockRejectedValue(new Error('Network error'));
    render(<InvestorProfileEditor />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('shows save error when updateProfile fails', async () => {
    mocks.getProfile.mockResolvedValue(MOCK_PROFILE);
    mocks.updateProfile.mockRejectedValue(new Error('Server error 500'));

    const user = userEvent.setup();
    render(<InvestorProfileEditor />);

    await waitFor(() => {
      expect(screen.queryByText(/loading investor profile/i)).not.toBeInTheDocument();
    });

    const goalTextarea = screen.getByDisplayValue('Long-term wealth compounding');
    await user.clear(goalTextarea);
    await user.type(goalTextarea, 'Changed');
    await user.click(screen.getByRole('button', { name: /save profile/i }));

    await waitFor(() => {
      expect(screen.getByText(/Save error: Server error 500/i)).toBeInTheDocument();
    });
  });
});
