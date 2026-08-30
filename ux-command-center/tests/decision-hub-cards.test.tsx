import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DecisionHub } from '../pages/DecisionHub';

const apiMocks = vi.hoisted(() => ({
  getDecisionsTimeline: vi.fn(),
  getDecisionsStats: vi.fn(),
  getDecisionsScorecard: vi.fn(),
  getDecisionsIntelligence: vi.fn(),
  getDecisionAlerts: vi.fn(),
}));

vi.mock('../src/services/api', async () => {
  const actual = await vi.importActual('../src/services/api');
  return { ...actual, api: apiMocks };
});

beforeEach(() => {
  apiMocks.getDecisionsTimeline.mockResolvedValue({
    items: [
      {
        id: 'insight_1',
        type: 'insight',
        date: '2026-03-16',
        title: 'AMZN RSU 归属 100% 变现',
        content: 'AMZN RSU 归属 100% 变现',
        source: 'memo',
        display_source: 'memo',
        status: 'adopted',
        display_status: 'adopted',
        subtype: 'recommendation',
        match_status: null,
        origin_ref: 'insights:1',
        metadata: { category: 'recommendation', tags: [] },
      },
    ],
    summary: { total: 44, adopted: 11, pending: 9 },
  });
  apiMocks.getDecisionsStats.mockResolvedValue({
    total_insights: 21,
    adopted_count: 11,
    pending_count: 9,
    pending_actions_count: 5,
    adoption_rate: 52.4,
    total_trades: 23,
    active_drift_alerts: 2,
  });
  apiMocks.getDecisionsScorecard.mockResolvedValue({
    items: [
      {
        id: 1,
        date: '2026-03-05',
        asset_id: 'US_STK_SGOV',
        asset_name: 'SGOV',
        action: 'Buy',
        source: 'memo',
        verification_result: null,
        verdict: null,
        outcome_pct: null,
        grade: null,
        match_status: 'matched',
        why_unscored: 'awaiting_verification_window',
        linked_insight_title: '建立通过 Schwab 通道离岸布局打折美股防线的方案',
      },
    ],
  });
  apiMocks.getDecisionsIntelligence.mockResolvedValue({
    decision_patterns: {
      funnel: { total: 21, adopted: 11, rejected: 1, pending: 9, good_call: 0, regret: 0, missed_opportunity: 0, bullet_dodged: 0 },
      leaderboard: [],
      sources: [{ source: 'memo', total: 11, adopted: 11, rejected: 0, pending: 0 }],
    },
    growth_timeline: [],
    raw_sections: [],
  });
  apiMocks.getDecisionAlerts.mockResolvedValue({
    alerts: [
      { category: 'drift', priority: 'high', title: 'Equity allocation drifted 37.0% from strategic target', message: 'Current: 33.0% | Target: 70.0%' },
      { category: 'drift', priority: 'medium', title: 'Fixed Income allocation drifted 8.6% from strategic target', message: 'Current: 11.4% | Target: 20.0%' },
      { category: 'strategy', priority: 'low', title: 'Strategy memo: 投资备忘录 006', message: 'Review memo' },
    ],
    counts: { high: 1, medium: 1, low: 1 },
  });

  Element.prototype.scrollIntoView = vi.fn();
});

describe('DecisionHub cards', () => {
  it('navigates to verification page when adoption card is clicked', async () => {
    render(
      <MemoryRouter initialEntries={['/decisions']}>
        <Routes>
          <Route path="/decisions" element={<DecisionHub />} />
          <Route path="/verify" element={<div>Verification Destination</div>} />
        </Routes>
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: /adoption rate/i }));

    expect(await screen.findByText('Verification Destination')).toBeInTheDocument();
  });

  it('returns to timeline when total decisions card is clicked', async () => {
    render(
      <MemoryRouter>
        <DecisionHub />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: /scorecard/i }));
    expect(await screen.findByText('建立通过 Schwab 通道离岸布局打折美股防线的方案')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /total decisions/i }));

    expect((await screen.findAllByText('AMZN RSU 归属 100% 变现')).length).toBeGreaterThan(0);
  });

  it('filters active alerts when drift and pending action cards are clicked', async () => {
    render(
      <MemoryRouter>
        <DecisionHub />
      </MemoryRouter>
    );

    expect(await screen.findByText('Strategy memo: 投资备忘录 006')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /drift alerts/i }));

    expect(await screen.findByText('Equity allocation drifted 37.0% from strategic target')).toBeInTheDocument();
    expect(screen.queryByText('Strategy memo: 投资备忘录 006')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /pending actions/i }));

    expect(await screen.findByText('Strategy memo: 投资备忘录 006')).toBeInTheDocument();
  });
});
