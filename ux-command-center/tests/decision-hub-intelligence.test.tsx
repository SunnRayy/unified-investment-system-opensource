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
      {
        id: 'trade_2',
        type: 'trade',
        date: '2026-03-20',
        title: 'Order Executed: Buy US_STK_VOO',
        content: 'Buy 8 units @ 595',
        source: 'aia_trades_md',
        display_source: 'memo',
        status: 'executed',
        display_status: 'executed',
        subtype: 'trade',
        match_status: 'memo_linked',
        origin_ref: 'trade_logs:2',
        metadata: {
          asset_id: 'US_STK_VOO',
          action: 'Buy',
          amount: 4760,
          linked_title: '投资战略 Memo：滞胀恐慌下的防御与自动反击',
          linked_ref: 'strategy_memos:10',
          effective_source: 'memo',
          reason_excerpt: '战略备忘录 Memo 009: 梯队1 SPX <= 6500 -> 买入 VOO @ Limit $595.00',
        },
      },
    ],
    summary: { total: 2, adopted: 1, pending: 0 },
  });
  apiMocks.getDecisionsStats.mockResolvedValue({
    total_insights: 2,
    adopted_count: 1,
    pending_count: 0,
    adoption_rate: 50,
    total_trades: 1,
    active_drift_alerts: 0,
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
      funnel: { total: 2, adopted: 1, rejected: 0, pending: 1, good_call: 0, regret: 0, missed_opportunity: 0, bullet_dodged: 0, linked_adopted_trades: 1 },
      leaderboard: [{ source: 'memo', total: 1, scored: 0, good_call: 0, hit_rate: 0, avg_outcome_pct: null }],
      sources: [{ source: 'memo', total: 1, adopted: 1, rejected: 0, pending: 0, linked_trades: 1 }],
    },
    growth_timeline: [
      {
        id: 'lesson_1',
        date: '2026-03-16',
        title: 'RSU 纪律性变现',
        content: '展现了极强的防御纪律性',
        source: 'observation',
        origin_ref: 'insights:2',
      },
    ],
    raw_sections: [
      {
        section: '行为模式观察',
        title: '决策特征',
        content: '| 买入决策 | 防守置换 |',
        entry_count: 1,
      },
    ],
  });
  apiMocks.getDecisionAlerts.mockResolvedValue({ alerts: [], counts: { high: 0, medium: 0, low: 0 } });
});

describe('DecisionHub intelligence', () => {
  it('renders structured intelligence and raw sections without timeline lesson pollution', async () => {
    render(
      <MemoryRouter>
        <DecisionHub />
      </MemoryRouter>
    );

    expect((await screen.findAllByText('AMZN RSU 归属 100% 变现')).length).toBeGreaterThan(0);
    expect(await screen.findByText('投资战略 Memo：滞胀恐慌下的防御与自动反击')).toBeInTheDocument();
    expect(screen.getByText(/Memo 009: 梯队1 SPX <= 6500/i)).toBeInTheDocument();
    expect(screen.queryByText('RSU 纪律性变现')).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: /intelligence/i }));

    expect(await screen.findByText('RSU 纪律性变现')).toBeInTheDocument();
    expect(screen.getByText('行为模式观察')).toBeInTheDocument();
    expect((screen.getAllByText('memo')).length).toBeGreaterThan(0);
    expect((screen.getAllByText(/linked trades/i)).length).toBeGreaterThan(0);
  });
});
