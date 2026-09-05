import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '../test-utils';
import { Dashboard } from '../pages/Dashboard';
import { RiskMatrix } from '../pages/RiskMatrix';
import { Performance } from '../pages/Performance';
import { WealthOS } from '../pages/WealthOS';
import { Compass } from '../pages/Compass';
import { Verification } from '../pages/Verification';
import { DecisionHub } from '../pages/DecisionHub';
import { StrategyAlignment } from '../pages/StrategyAlignment';

const apiMocks = vi.hoisted(() => ({
  getKPI: vi.fn(),
  getInsights: vi.fn(),
  getAllocation: vi.fn(),
  getAuditSummary: vi.fn(),
  getPerformanceHistory: vi.fn(),
  getDashboardActions: vi.fn(),
  getRiskMetrics: vi.fn(),
  getRiskCorrelation: vi.fn(),
  getPerformanceSummary: vi.fn(),
  getGainsAnalysis: vi.fn(),
  getPerformanceByClass: vi.fn(),
  getWealthOSAssets: vi.fn(),
  getWealthOSSummary: vi.fn(),
  getCompassSummary: vi.fn(),
  getCompassAllocation: vi.fn(),
  getCompassMarkdown: vi.fn(),
  getLatestVerification: vi.fn(),
  triggerVerification: vi.fn(),
  getVerificationTrends: vi.fn(),
  getDecisionsTimeline: vi.fn(),
  getDecisionsStats: vi.fn(),
  getDecisionsScorecard: vi.fn(),
  getDecisionsIntelligence: vi.fn(),
  getDecisionsFunnel: vi.fn(),
  getDecisionsLeaderboard: vi.fn(),
  getDecisionAlerts: vi.fn(),
  getStrategyAlignment: vi.fn(),
  triggerStrategyReview: vi.fn(),
  getStrategyMemos: vi.fn(),
}));

const analyticsApiMocks = vi.hoisted(() => ({
  getMarketRegime: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
  api: apiMocks,
  AnalyticsAPI: analyticsApiMocks,
}));

beforeEach(() => {
  apiMocks.getKPI.mockResolvedValue({
    net_worth: 1000000,
    pnl_24h: 1200,
    market_pulse: 62,
    market_pulse_sentiment: 'Neutral',
  });
  apiMocks.getInsights.mockResolvedValue([]);
  apiMocks.getAllocation.mockResolvedValue([]);
  apiMocks.getAuditSummary.mockResolvedValue({
    total_logs: 0,
    last_sync_timestamp: '2026-02-14T10:00:00Z',
    unresolved_conflicts: 0,
  });
  apiMocks.getPerformanceHistory.mockResolvedValue([]);
  apiMocks.getDashboardActions.mockResolvedValue({ actions: [] });
  apiMocks.getRiskMetrics.mockResolvedValue({ volatility: 0, sharpe: 0, var_95: 0, beta: 1, div_score: 5 });
  apiMocks.getRiskCorrelation.mockResolvedValue({ matrix: [], assets: [], method: 'pearson' });
  apiMocks.getPerformanceSummary.mockResolvedValue({
    net_worth: 1000000,
    total_cost_basis: 900000,
    total_unrealized_pl: 50000,
    unrealized_pl_pct: 5,
    total_realized_pl: 20000,
    total_lifetime_pl: 70000,
    asset_count: 2,
    snapshot_date: '2026-02-14',
  });
  apiMocks.getGainsAnalysis.mockResolvedValue({
    total_unrealized_pl: 50000,
    total_realized_pl: 20000,
    total_lifetime_pl: 70000,
    total_cost_basis: 900000,
    total_market_value: 1000000,
    unrealized_pl_pct: 5,
    assets: [],
  });
  apiMocks.getPerformanceByClass.mockResolvedValue({
    total_market_value: 1000000,
    total_cost_basis: 900000,
    top_classes: [],
    sub_classes: [],
  });
  apiMocks.getWealthOSAssets.mockResolvedValue([]);
  apiMocks.getWealthOSSummary.mockResolvedValue({
    total_lifetime_gain: 0,
    lifetime_gain_pct: 0,
    annualized_return: null,
    active_asset_count: 0,
    total_asset_count: 0,
  });
  apiMocks.getCompassSummary.mockResolvedValue({
    total_net_worth: 1000000,
    drift_index: 1,
    classes_in_drift: 0,
    total_classes: 5,
    last_sync_date: '2026-02-14',
    last_sync_source: 'test',
  });
  apiMocks.getCompassAllocation.mockResolvedValue({ rows: [], meta: null });
  apiMocks.getCompassMarkdown.mockResolvedValue({
    top_level_table: 'top',
    sub_class_table: 'sub',
    generated_at: '2026-02-14',
  });
  apiMocks.getLatestVerification.mockResolvedValue({
    period_start: '2026-02-01',
    period_end: '2026-02-14',
    adoption_rate: 50,
    portfolio_return: 2,
    benchmark_return: 1,
    alpha: 1,
    max_drift: 3,
    total_insights: 10,
    verdict_hit_rate: 50,
    adoption_history: [{
      period_start: '2026-01-01',
      adoption_rate: 50,
      total: 10,
      adopted: 5,
    }],
    verdict_breakdown: [],
  });
  apiMocks.triggerVerification.mockResolvedValue({
    period_start: '2026-02-01',
    period_end: '2026-02-14',
    adoption_rate: 50,
    portfolio_return: 2,
    benchmark_return: 1,
    alpha: 1,
    max_drift: 3,
    total_insights: 10,
    verdict_hit_rate: 50,
    adoption_history: [],
    verdict_breakdown: [],
  });
  apiMocks.getVerificationTrends.mockResolvedValue({
    periods: [{
      period_start: '2026-01-01',
      period_end: '2026-01-31',
      adoption_rate: 50,
      portfolio_return: 2,
      benchmark_return: 1,
      alpha: 1,
      max_drift: 3,
      total_insights: 10,
    }],
  });
  apiMocks.getDecisionsTimeline.mockResolvedValue({
    items: [],
    summary: { total: 0, adopted: 0, pending: 0 },
  });
  apiMocks.getDecisionsStats.mockResolvedValue({
    total_insights: 0,
    adopted_count: 0,
    pending_count: 0,
    pending_actions_count: 0,
    adoption_rate: 0,
    total_trades: 0,
    active_drift_alerts: 0,
    ai_trades_total: 0,
  });
  apiMocks.getDecisionsScorecard.mockResolvedValue({ items: [] });
  apiMocks.getDecisionsIntelligence.mockResolvedValue({
    decision_patterns: {
      funnel: {
        total: 0, adopted: 0, rejected: 0, pending: 0,
        good_call: 0, regret: 0, missed_opportunity: 0, bullet_dodged: 0,
      },
      leaderboard: [],
      sources: [],
    },
    growth_timeline: [],
    raw_sections: [],
  });
  apiMocks.getDecisionsFunnel.mockResolvedValue({
    total: 0, adopted: 0, rejected: 0, pending: 0,
    good_call: 0, regret: 0, missed_opportunity: 0, bullet_dodged: 0,
  });
  apiMocks.getDecisionsLeaderboard.mockResolvedValue({ sources: [] });
  apiMocks.getDecisionAlerts.mockResolvedValue({ alerts: [], counts: { high: 0, medium: 0, low: 0 } });
  apiMocks.getStrategyAlignment.mockResolvedValue({ report: null, message: 'No review data.' });
  apiMocks.triggerStrategyReview.mockResolvedValue({
    status: 'ok',
    report: {
      review_date: '2026-02-14',
      target_scope_alignment: {},
      uis_scope_alignment: {},
      target_scope_summary: { included_classes: [], excluded_classes: [], coverage_note: 'Strategic note' },
      uis_scope_summary: { included_classes: [], excluded_classes: [], coverage_note: 'Huinsight note' },
      trading_frequency: {
        period_30d: 0,
        period_60d: 0,
        period_90d: 0,
        monthly_rate: 0,
        assessment: 'aligned',
        philosophy_threshold: 4,
      },
      contrarian_score: null,
      contrarian_details: { status: 'insufficient_market_context', sell_count: 0, panic_sell_count: 0, details: [] },
      profile_discrepancies: { uis_only: [], both: [] },
      target_scope_alignment_status: 'aligned',
      uis_scope_alignment_status: 'aligned',
    },
  });
  apiMocks.getStrategyMemos.mockResolvedValue({ memos: [] });

  analyticsApiMocks.getMarketRegime.mockResolvedValue({
    trend: 'Neutral',
    volatility_level: 'Normal',
    volatility_30d: 0.15,
    drawdown_pct: -0.05,
    ma50: 100,
    ma200: 95,
    ma_signal: 'Neutral',
    score: 50,
    data_points: 252,
    benchmark_symbol: 'SPY'
  });
});

describe('route-level page smoke coverage', () => {
  it('dashboard renders in day shell', async () => {
    render(<Dashboard />);
    const page = await screen.findByTestId('dashboard-page');
    expect(page.className).toContain('bg-gray-50');
    expect(screen.getByText('Unified Portfolio')).toBeInTheDocument();
  });

  it('risk matrix renders in day shell', async () => {
    render(<RiskMatrix />);
    const page = await screen.findByTestId('risk-page');
    expect(page.className).toContain('bg-gray-50');
    expect(screen.getByText('Risk Matrix & Sensitivity Analysis')).toBeInTheDocument();
  });

  it('performance renders in day shell', async () => {
    render(<Performance />);
    const page = await screen.findByTestId('performance-page');
    expect(page.className).toContain('bg-gray-50');
    expect(screen.getByText('Performance Analysis')).toBeInTheDocument();
  });

  it('wealthos renders in day shell', async () => {
    render(<WealthOS />);
    const page = await screen.findByTestId('wealth-page');
    expect(page.className).toContain('bg-gray-50');
    expect(screen.getByText('Lifetime Asset Performance')).toBeInTheDocument();
  });

  it('compass renders in day shell', async () => {
    render(<Compass />);
    const page = await screen.findByTestId('compass-page');
    expect(page.className).toContain('bg-gray-50');
    // Program BIL / WS-2: owner-approved rename, EN "Compass Report" →
    // "Allocation Report" (docs/i18n-glossary.md), to match the already-
    // shipped zh-CN "资产配置报告". The one deliberate exception to this
    // program's byte-identical-EN rule.
    expect(screen.getByText('Hierarchical Allocation Report')).toBeInTheDocument();
  });

  it('verification renders in day shell', async () => {
    render(<Verification />);
    const page = await screen.findByTestId('verification-page');
    expect(page.className).toContain('bg-gray-50');
    expect(page.className).toContain('w-full');
    expect(page.className).toContain('max-w-[1600px]');
    expect(screen.getByText('Review Center')).toBeInTheDocument();
    expect(screen.getByText('Verification Dashboard for adoption, verdicts, and drift.')).toBeInTheDocument();
    expect(screen.queryByText(/Insufficient data\. Run verification/i)).not.toBeInTheDocument();
  });

  it('decision hub renders in day shell', async () => {
    render(<DecisionHub />);
    const page = await screen.findByTestId('decision-page');
    expect(page.className).toContain('bg-gray-50');
    expect(screen.getByText('Decision Hub')).toBeInTheDocument();
  });

  it('strategy alignment renders empty state in day shell', async () => {
    render(<StrategyAlignment />);
    const page = await screen.findByTestId('strategy-page');
    expect(page.className).toContain('bg-gray-50');
    expect(page.className).toContain('w-full');
    expect(page.className).toContain('max-w-[1600px]');
    expect(screen.getByText('Strategy Alignment')).toBeInTheDocument();
  });
});
