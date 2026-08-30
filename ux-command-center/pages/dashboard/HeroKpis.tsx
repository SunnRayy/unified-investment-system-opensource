import React from 'react';
import { Sparkline } from './DashboardCharts';
import { KPI, PerformanceReturns, MarketStatusComposite, CashFlowAnalysis } from '../../src/services/api';
import { useCurrency } from '../../src/context/useCurrency';
import { useLanguage } from '../../src/context/useLanguage';
import { useTranslation } from 'react-i18next';
import { formatMoneyStr, formatPercent, type UiLocale } from '../../src/utils/formatMoney';

/**
 * makeFmtCNY — factory for the compact money formatter used in HeroKpis and NetWorthTrend.
 * Returns a plain string (no JSX) suitable for inline style-concatenated labels.
 * Accepts `convertFromCNY` and `symbol` from CurrencyContext so it respects
 * the active reporting currency.
 *
 * Program BIL / WS-2: delegates the actual number formatting to
 * `src/utils/formatMoney.ts` (WS-1) instead of a hand-rolled K/M string — the
 * `lang` param is what makes the compact suffix 万/亿 for zh-CN instead of
 * always English K/M. Kept as a factory (rather than calling formatMoneyStr
 * inline) so NetWorthTrend.tsx's existing call shape keeps working.
 *
 * NOTE: fmtCNY is still exported for backward-compat with NetWorthTrend.tsx.
 * New call sites should use makeFmtCNY or useFormatCurrency().
 */
export function makeFmtCNY(
  convertFromCNY: (v: number) => number,
  symbol: string,
  lang: UiLocale = 'en',
) {
  return (n: number, opts: { signed?: boolean; compact?: boolean } = {}): string => {
    const { compact = true, signed = false } = opts;
    return formatMoneyStr(convertFromCNY(n), lang, { compact, signed, symbol, signStyle: 'prefix' });
  };
}

/**
 * fmtCNY — legacy CNY-only compact formatter.
 * Used by NetWorthTrend (imported from this file).  Do not remove.
 * Only produces ¥-denominated output regardless of reporting currency;
 * NetWorthTrend is migrated to use makeFmtCNY via useCurrency internally.
 *
 * The local K/M string-building is gone (Program BIL / WS-2 formatter fold-in,
 * WS-1 handoff) — this is now a thin wrapper that preserves the
 * `compact` default of `true` (a dashboard-specific default; formatMoney.ts's
 * own `fmtCNY` compat export defaults `compact` to `false`) and otherwise
 * delegates straight to `formatMoneyStr`.
 */
const fmtCNY = (n: number, opts: { signed?: boolean; compact?: boolean; lang?: UiLocale } = {}) => {
  const { compact = true, signed = false, lang = 'en' } = opts;
  return formatMoneyStr(n, lang, { compact, signed, symbol: '¥', signStyle: 'prefix' });
};

/** Program BIL / WS-2: identical defaults to the deleted local implementation
 *  (signed=true, digits=1, U+2212 minus) — a straight import swap. */
const fmtPct = formatPercent;

export { fmtCNY, fmtPct };

interface HeroProps {
  kpi: KPI | null;
  perfReturns: PerformanceReturns | null;
  marketStatus: MarketStatusComposite | null;
  cashFlow: CashFlowAnalysis | null;
  sparkData: number[];
  delta30d: number | null;
  pct30d: number | null;
  sinceLabel: string;
  macro?: { usd_cny?: number | null; source?: string; fallback_used?: boolean } | null;
}

const cardStyle: React.CSSProperties = {
  background: 'var(--color-card)',
  border: '1px solid var(--color-border)',
  borderRadius: 12,
  boxShadow: 'var(--shadow-sm)',
  padding: 20,
};

const eyebrowStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const,
  letterSpacing: '0.05em', color: 'var(--color-fg-3)',
};

const monoKpi: React.CSSProperties = {
  fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 24,
};

const subLabel: React.CSSProperties = {
  fontSize: 9, fontWeight: 700, color: 'var(--color-fg-4)', letterSpacing: '0.06em',
};

const subVal = (color: string): React.CSSProperties => ({
  fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12, color,
});

export const HeroKpis: React.FC<HeroProps> = ({
  kpi, perfReturns, marketStatus, cashFlow, sparkData, delta30d, pct30d, sinceLabel, macro,
}) => {
  const { convertFromCNY, currencySymbol, currency } = useCurrency();
  const { lang } = useLanguage();
  const { t } = useTranslation('portfolio');
  const fmt = makeFmtCNY(convertFromCNY, currencySymbol, lang);
  const ytdPct = perfReturns?.twr_ytd;
  const xirr = perfReturns?.mwr_xirr;

  // Cash buffer: compute months of cash from cashflow
  const avgExpense = cashFlow?.trends?.avg_expense ?? 0;
  const cashHoldings = kpi?.cash_available ?? 0;
  const monthsCash = avgExpense > 0 ? cashHoldings / avgExpense : 0;
  const cashTarget = 6;
  const cashPct = Math.min(100, (monthsCash / cashTarget) * 100);
  const isCachedRate = macro?.source?.includes('cache') ?? false;

  const verdictColor = !marketStatus ? 'var(--color-fg-4)' :
    marketStatus.verdict_color === 'green' || marketStatus.verdict_color === 'light-green' ? 'var(--color-success)' :
    marketStatus.verdict_color === 'yellow' ? 'var(--zone-yellow-fg)' :
    marketStatus.verdict_color === 'orange' ? 'var(--zone-orange-fg)' :
    'var(--zone-red-fg)';

  return (
    <>
    <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 16 }}>
      {/* Net Worth */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <span style={eyebrowStyle}>{t('dashboard.hero.netWorth')}</span>
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
            background: 'var(--fill-neutral-soft)', color: 'var(--color-fg-3)',
          }}>{t('dashboard.hero.days30')}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: 8, gap: 12 }}>
          <div>
            <div className="money-value" style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 28, letterSpacing: '-0.01em' }}>
              {kpi ? fmt(kpi.net_worth) : '...'}
            </div>
            <div style={{ display: 'flex', gap: 14, marginTop: 8 }}>
              <div>
                <div style={subLabel}>{t('dashboard.hero.ytd')}</div>
                <div style={subVal(ytdPct != null ? (ytdPct >= 0 ? 'var(--color-success)' : 'var(--color-danger)') : 'var(--color-fg-4)')}>
                  {ytdPct != null ? fmtPct(ytdPct) : t('dashboard.hero.notAvailable')}
                </div>
              </div>
              <div>
                <div style={subLabel}>{t('dashboard.hero.xirr')}</div>
                <div style={subVal(xirr != null ? (xirr >= 0 ? 'var(--color-success)' : 'var(--color-danger)') : 'var(--color-fg-4)')}>
                  {xirr != null ? fmtPct(xirr) : t('dashboard.hero.notAvailable')}
                </div>
              </div>
            </div>
          </div>
          {sparkData.length > 1 && <Sparkline data={sparkData} width={140} height={48} color="#3b82f6" />}
        </div>
      </div>

      {/* Vs Last Month */}
      <div style={cardStyle}>
        <span style={eyebrowStyle}>{t('dashboard.hero.vsLastMonth')}</span>
        <div className="money-value" style={{ ...monoKpi, marginTop: 8, color: delta30d != null ? (delta30d >= 0 ? 'var(--color-success)' : 'var(--color-danger)') : 'var(--color-fg-4)' }}>
          {delta30d != null ? fmt(delta30d, { signed: true }) : t('dashboard.hero.notAvailable')}
        </div>
        <div style={{ display: 'flex', gap: 14, marginTop: 8 }}>
          <div>
            <div style={subLabel}>{t('dashboard.hero.change')}</div>
            <div style={subVal(pct30d != null ? (pct30d >= 0 ? 'var(--color-success)' : 'var(--color-danger)') : 'var(--color-fg-4)')}>
              {pct30d != null ? fmtPct(pct30d) : t('dashboard.hero.notAvailable')}
            </div>
          </div>
          <div>
            <div style={subLabel}>{t('dashboard.hero.since')}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 500, fontSize: 12, color: 'var(--color-fg-2)' }}>
              {sinceLabel}
            </div>
          </div>
        </div>
      </div>

      {/* Cash Buffer */}
      <div style={cardStyle}>
        <span style={eyebrowStyle}>{t('dashboard.hero.cashBuffer')}</span>
        <div style={{ ...monoKpi, marginTop: 8 }}>
          {monthsCash > 0 ? monthsCash.toFixed(1) : '—'}
          <span style={{ fontSize: 13, color: 'var(--color-fg-3)', marginLeft: 4 }}>{t('dashboard.hero.moSuffix')}</span>
        </div>
        <div style={{ marginTop: 8 }}>
          <div style={{ height: 4, background: 'var(--color-border-soft)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{ width: `${cashPct}%`, height: '100%', background: cashPct > 60 ? 'var(--color-success)' : 'var(--color-warning)' }} />
          </div>
          <div style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 6, fontFamily: 'var(--font-mono)' }}>
            {t('dashboard.hero.cashTargetLine', { target: cashTarget.toFixed(1), pct: cashPct.toFixed(0) })}
          </div>
        </div>
      </div>

      {/* Market Status */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <span style={eyebrowStyle}>{t('dashboard.hero.marketStatus')}</span>
          <a href="/market-sentiment" style={{ fontSize: 11, color: 'var(--color-primary)', textDecoration: 'none', fontWeight: 500 }}>{t('dashboard.hero.detailsLink')}</a>
        </div>
        <div style={{ fontWeight: 700, fontSize: 24, color: verdictColor, marginTop: 8 }}>
          {marketStatus?.verdict ?? t('dashboard.hero.notAvailable')}
        </div>
        <div style={{ display: 'flex', gap: 14, marginTop: 8 }}>
          <div>
            <div style={subLabel}>{t('dashboard.hero.signals')}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12 }}>
              {marketStatus ? (
                <>
                  <span style={{ color: marketStatus.red_count > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>
                    {marketStatus.red_count > 0 ? t('dashboard.hero.redCount', { count: marketStatus.red_count }) : t('dashboard.hero.allClear')}
                  </span>
                  <span style={{ color: 'var(--color-fg-4)', marginLeft: 6 }}>{t('dashboard.hero.totalCount', { count: marketStatus.total_count })}</span>
                </>
              ) : <span style={{ color: 'var(--color-fg-4)' }}>{t('dashboard.hero.noData')}</span>}
            </div>
          </div>
        </div>
      </div>
    </div>
    {/* FX Rate Strip */}
    {macro?.usd_cny != null && (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        marginTop: 8, padding: '6px 12px',
        borderRadius: 8,
        background: 'var(--fill-neutral-soft)',
        fontSize: 11,
      }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span className="material-symbols-outlined" style={{ fontSize: 14, color: 'var(--color-fg-3)' }}>currency_exchange</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--color-fg-2)' }}>
            {t('dashboard.hero.usdCnyLabel', { rate: macro.usd_cny.toFixed(4) })}
          </span>
        </span>
        <span style={{
          fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 4,
          background: macro.fallback_used ? 'var(--zone-orange-bg)' : 'var(--color-success-soft, rgba(34,197,94,0.1))',
          color: macro.fallback_used ? 'var(--zone-orange-fg)' : 'var(--color-success)',
          textTransform: 'uppercase' as const,
          letterSpacing: '0.04em',
        }}>
          {macro.fallback_used ? t('dashboard.hero.fallback') : isCachedRate ? t('dashboard.hero.cached') : t('dashboard.hero.live')}
        </span>
        <span style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
          {currency === 'USD'
            ? t('dashboard.hero.displayingUsd')
            : t('dashboard.hero.displayingCny')}
        </span>
      </div>
    )}
    </>
  );
};
