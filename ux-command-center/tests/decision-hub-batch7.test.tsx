import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { DecisionHub } from '../pages/DecisionHub';

const apiMocks = vi.hoisted(() => ({
  getDecisionsTimeline: vi.fn(),
  getDecisionsStats: vi.fn(),
  getDecisionsScorecard: vi.fn(),
  getDecisionsIntelligence: vi.fn(),
  getDecisionsFunnel: vi.fn(),
  getDecisionsLeaderboard: vi.fn(),
  getDecisionAlerts: vi.fn(),
}));

vi.mock('../src/services/api', async () => {
  const actual = await vi.importActual('../src/services/api');
  return { ...actual, api: apiMocks };
});

beforeEach(() => {
  apiMocks.getDecisionsTimeline.mockResolvedValue({
    items: [],
    summary: { total: 0, adopted: 0, pending: 0 },
  });
  apiMocks.getDecisionsStats.mockResolvedValue({
    total_insights: 1,
    adopted_count: 1,
    pending_count: 0,
    adoption_rate: 100,
    total_trades: 1,
    active_drift_alerts: 0,
  });
  apiMocks.getDecisionsScorecard.mockResolvedValue({
    items: [
      {
        id: 1,
        date: '2026-01-13',
        asset_id: 'US_ETF_SGOV',
        asset_name: 'SGOV',
        action: 'Buy',
        price: 100,
        quantity: 10,
        amount: 1000,
        source: 'gemini',
        verification_date: '2026-02-12',
        verification_result: '验证通过',
        verdict: 'good_call',
        outcome_pct: 10,
        grade: null,
      },
    ],
  });
  apiMocks.getDecisionsIntelligence.mockResolvedValue({
    decision_patterns: {
      funnel: {
        total: 1,
        adopted: 1,
        rejected: 0,
        pending: 0,
        good_call: 1,
        regret: 0,
        missed_opportunity: 0,
        bullet_dodged: 0,
      },
      leaderboard: [],
      sources: [{ source: 'gemini', total: 1, adopted: 1, rejected: 0, pending: 0 }],
    },
    growth_timeline: [],
    raw_sections: [],
  });
  apiMocks.getDecisionsFunnel.mockResolvedValue({
    total: 1,
    adopted: 1,
    rejected: 0,
    pending: 0,
    good_call: 1,
    regret: 0,
    missed_opportunity: 0,
    bullet_dodged: 0,
  });
  apiMocks.getDecisionsLeaderboard.mockResolvedValue({ sources: [] });
  apiMocks.getDecisionAlerts.mockResolvedValue({ alerts: [], counts: { high: 0, medium: 0, low: 0 } });
});

describe('DecisionHub batch7', () => {
  it('shows grade tooltip when grade is missing', async () => {
    render(
      <MemoryRouter>
        <DecisionHub />
      </MemoryRouter>
    );

    const tab = await screen.findByRole('button', { name: /scorecard/i });
    fireEvent.click(tab);

    expect(
      await screen.findByTitle('No review recorded for this trade')
    ).toBeInTheDocument();
  });
});
