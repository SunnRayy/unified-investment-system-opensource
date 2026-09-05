import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';

import { BriefSection } from '../components/ai-advisor/BriefSection';
import i18n from '../src/i18n';

describe('Review section rendering', () => {
  it('renders review trade rows with asset, date, reasoning, and grade breakdown details', () => {
    render(
      <BriefSection
        title="trade_summary"
        content={{
          narrative: 'review summary',
          trades: [
            {
              date: '2026-03-20',
              asset: 'VOO',
              action: 'Buy',
              logic: '触发SPX 6500点防御线自动成交',
            },
          ],
          grade_breakdown: {
            'A (卓越执行)': 'VOO自动挂单成交',
            '待改进': '现金工具操作频率过高',
          },
        }}
      />
    );

    expect(screen.getByText('VOO')).not.toBeNull();
    expect(screen.getByText('2026-03-20')).not.toBeNull();
    expect(screen.getByText(/触发SPX 6500点防御线自动成交/)).not.toBeNull();
    expect(screen.getByText('A (卓越执行)')).not.toBeNull();
    expect(screen.getByText('VOO自动挂单成交')).not.toBeNull();
  });

  it('renders grade_breakdown count map as compact chips, not stacked key/value boxes (GitHub #26)', () => {
    const { container } = render(
      <BriefSection
        title="trade_summary"
        content={{
          narrative: 'review summary',
          trades: [{ date: '2026-06-25', asset: 'MSFT', action: 'Buy', grade: 'A' }],
          grade_breakdown: { A: 2, B: 2, C: 2 },
        }}
      />
    );

    // The grade letters and counts render as a single compact "×2" chip each,
    // not as the old "A" header with a "2" detail box underneath.
    expect(screen.getByText('Grade distribution')).not.toBeNull();
    const chipTexts = Array.from(container.querySelectorAll('span'))
      .map((el) => el.textContent || '')
      .filter((t) => t.includes('×2'));
    expect(chipTexts.length).toBeGreaterThanOrEqual(3); // A×2, B×2, C×2
  });

  it('renders review scorecard rows with target, status, and comment details', () => {
    render(
      <BriefSection
        title="advice_accuracy"
        content={{
          narrative: 'review scorecard',
          scorecard: [
            {
              target: 'AMZN风险控制',
              status: '高准确度',
              comment: '成功降低RSU集中度风险，符合能力圈保护原则',
            },
          ],
        }}
      />
    );

    expect(screen.getByText('AMZN风险控制')).not.toBeNull();
    expect(screen.getByText('高准确度')).not.toBeNull();
    expect(screen.getByText(/成功降低RSU集中度风险/)).not.toBeNull();
  });

  // ── New tests pinning the exact id=48 production shapes (GitHub #26 reopened) ──

  it('renders nested grade_breakdown (deepseek id=48 shape) with chips, labeled count and notes — no raw JSON', () => {
    const { container } = render(
      <BriefSection
        title="trade_summary"
        content={{
          narrative: '本期12笔交易',
          grade_breakdown: {
            total_trades: 12,
            grades: { 'N/A': 12 },
            notes: '所有交易均为N/A等级，未能形成有效评价',
          },
        }}
      />
    );

    // N/A × 12 chip should render (from nested grades count-map)
    const chips = Array.from(container.querySelectorAll('span')).filter(
      (el) => el.textContent?.includes('×12')
    );
    expect(chips.length).toBeGreaterThanOrEqual(1);

    // total_trades label renders from the catalog (EN in tests)
    expect(container.textContent).toContain('Trades');

    // notes text renders
    expect(container.textContent).toContain('所有交易均为N/A等级，未能形成有效评价');

    // No raw JSON.stringify output (the original bug)
    expect(container.textContent).not.toContain('{"N/A"');
    expect(container.textContent).not.toContain('"N/A":12}');
  });

  it('renders scorecard with deepseek free-form keys using translated labels', () => {
    const { container } = render(
      <BriefSection
        title="advice_accuracy"
        content={{
          narrative: '准确性分析',
          scorecard: [
            {
              decision: '买入VOO',
              date: '2026-06-01',
              type: 'ETF买入',
              valuation_support: 'PE低于均值',
              execution_quality: '及时',
              logic_consistency: '高',
              verdict: '决策合理',
            },
          ],
        }}
      />
    );

    // Translated labels render (KEY_LABELS map)
    expect(container.textContent).toContain('Decision');
    expect(container.textContent).toContain('Valuation basis');
    expect(container.textContent).toContain('Verdict');

    // Values render in their own spans
    expect(screen.getByText('买入VOO')).not.toBeNull();
    expect(screen.getByText('PE低于均值')).not.toBeNull();
    expect(screen.getByText('决策合理')).not.toBeNull();
  });

  it('suppresses N/A grade chip on trade rows (render nothing)', () => {
    const { container } = render(
      <BriefSection
        title="trade_summary"
        content={{
          trades: [
            { date: '2026-06-01', asset: 'SGOV', action: 'Buy', grade: 'N/A', logic: '常规配置' },
          ],
        }}
      />
    );

    // Trade row renders (asset visible)
    expect(screen.getByText('SGOV')).not.toBeNull();

    // No rounded-full span with text 'N/A' (the chip should not render)
    const naChips = Array.from(container.querySelectorAll('span')).filter(
      (el) => el.textContent?.trim() === 'N/A' && el.className.includes('rounded-full')
    );
    expect(naChips.length).toBe(0);
  });

  it('skips scorecard item with all null/empty fields — no empty box rendered', () => {
    const { container } = render(
      <BriefSection
        title="advice_accuracy"
        content={{
          scorecard: [
            { target: '', status: null, score: null, comment: '' },
          ],
        }}
      />
    );

    // No scorecard row (border-b) should be rendered — item was fully skipped
    const rows = container.querySelectorAll('.border-b');
    expect(rows.length).toBe(0);
  });

  it('renders post-normalizer section-level scalars (total_trades, notes) with translated labels', () => {
    render(
      <BriefSection
        title="trade_summary"
        content={{
          narrative: '本期共12笔交易。',
          trades: [
            { date: '2026-06-05', asset: '900003', action: 'Sell', grade: 'N/A' },
          ],
          grade_breakdown: { 'N/A': 12 },
          total_trades: 12,
          notes: '所有交易均未标注评级，无法通过系统规则验证交易纪律。',
        }}
      />
    );

    // Scalar leafs render with translated labels — they must NOT vanish
    expect(screen.getByText('Trades:')).not.toBeNull();
    expect(screen.getByText('12')).not.toBeNull();
    expect(screen.getByText('Notes:')).not.toBeNull();
    expect(screen.getByText(/所有交易均未标注评级/)).not.toBeNull();

    // No raw key names anywhere
    expect(screen.queryByText('total_trades')).toBeNull();
    expect(screen.queryByText('notes')).toBeNull();
  });
});

/**
 * Program BIL / WS-5 — section identity is a stable ASCII ID, and the DISPLAY
 * label comes from the catalog. The model's output language must not be able to
 * change which section a payload lands in, or how it is styled.
 */
describe('Section identity and labels', () => {
  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage('en');
    });
  });

  it('resolves the display label from the catalog, not from the payload', () => {
    render(<BriefSection title="macro_outlook" content={{ narrative: 'Markets are calm.' }} />);

    expect(screen.getByText('Macro outlook')).not.toBeNull();
    // The machine ID must never reach the reader.
    expect(screen.queryByText('macro_outlook')).toBeNull();
  });

  it('renders the same ID in Chinese when the locale changes', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh-CN');
    });

    render(<BriefSection title="macro_outlook" content={{ narrative: '全球市场稳定。' }} />);

    // Same ID, same payload, Chinese label — the identity did not move.
    expect(screen.getByText('宏观形势')).not.toBeNull();
  });

  it('renders an unrecognised section key verbatim instead of blanking it', () => {
    // A hand-edited payload, or a section we have never seen. AGENTS.md Rule 12:
    // degrade to the raw key, never to an empty card.
    render(<BriefSection title="某个未知章节" content={{ narrative: 'still visible' }} />);

    expect(screen.getByText('某个未知章节')).not.toBeNull();
    expect(screen.getByText('still visible')).not.toBeNull();
  });

  it('styles the accuracy badge off the accuracy_tier enum, not off matched prose', () => {
    const { container } = render(
      <BriefSection
        title="advice_accuracy"
        content={{
          narrative: 'scorecard',
          scorecard: [
            { decision: 'Trimmed MSFT', accuracy_tier: 'high', verdict: 'Right call' },
            { decision: 'Held cash', accuracy_tier: 'low', verdict: 'Too slow' },
          ],
        }}
      />
    );

    expect(screen.getByText('High accuracy')).not.toBeNull();
    expect(screen.getByText('Low accuracy')).not.toBeNull();
    // high → emerald, low → red. Colour is keyed off the enum value.
    expect(container.querySelector('.bg-emerald-100')).not.toBeNull();
    expect(container.querySelector('.bg-red-100')).not.toBeNull();
    // The enum itself is never shown as a raw field row.
    expect(screen.queryByText('accuracy_tier:')).toBeNull();
  });

  it('gives an unknown accuracy_tier neutral styling rather than dropping the row', () => {
    render(
      <BriefSection
        title="advice_accuracy"
        content={{ scorecard: [{ decision: 'Something', accuracy_tier: 'unheard-of' }] }}
      />
    );

    expect(screen.getByText('unheard-of')).not.toBeNull();
    expect(screen.getByText('Something')).not.toBeNull();
  });

  it('styles action and status badges off their enums', () => {
    const { container } = render(
      <BriefSection
        title="action_items"
        content={{
          actions: [{ asset: 'VOO', action: 'buy', reasoning: 'cheap' }],
        }}
      />
    );

    expect(screen.getByText('BUY')).not.toBeNull();
    // buy → emerald left border, which the old 买入/买 prose could never match.
    expect(container.querySelector('.border-l-emerald-400')).not.toBeNull();
  });
});
