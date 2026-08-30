import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { AreaChart } from './DashboardCharts';
import { fmtPct, makeFmtCNY } from './HeroKpis';
import { HistoryItem, api, PerformanceRiskMetrics } from '../../src/services/api';
import { usePortfolioFilter } from '../../src/context/usePortfolioFilter';
import { useCurrency } from '../../src/context/useCurrency';
import { useLanguage } from '../../src/context/useLanguage';

interface Props {
  performanceHistory: HistoryItem[];
}

/** Period codes — kept identical in both locales; hoisted out of any JSX
 *  child-expression container (same reasoning as DashboardCards.tsx's
 *  MOVER_TAB_ORDER) since digit-leading codes like '1Y' don't match the
 *  i18n ratchet's ALL-CAPS CODE_LIKE allowance. */
const PERIOD_ORDER = ['1Y', '2Y', '5Y', 'ALL'] as const;

export const NetWorthTrend: React.FC<Props> = ({ performanceHistory }) => {
  const { t } = useTranslation('portfolio');
  const { lang } = useLanguage();
  const [period, setPeriod] = useState<'1Y' | '2Y' | '5Y' | 'ALL'>('ALL');
  const [volatility, setVolatility] = useState<number | null>(null);
  const { includeNonRebalanceable } = usePortfolioFilter();
  const { convertFromCNY, currencySymbol } = useCurrency();
  const fmtCNY = makeFmtCNY(convertFromCNY, currencySymbol, lang);

  useEffect(() => {
    let apiPeriod = 'all_time';
    if (period === '1Y') apiPeriod = '1y';
    if (period === '2Y') apiPeriod = '2y';
    if (period === '5Y') apiPeriod = '5y';

    api.getPerformanceRiskMetrics(apiPeriod, includeNonRebalanceable)
      .then(res => setVolatility(res.volatility_annual))
      .catch(() => setVolatility(null));
  }, [period, includeNonRebalanceable]);

  const allSeries = performanceHistory.map(h => ({ m: h.name, v: h.value }));
  if (allSeries.length < 2) {
    return (
      <div style={{
        background: 'var(--color-card)', border: '1px solid var(--color-border)',
        borderRadius: 12, boxShadow: 'var(--shadow-sm)', padding: 20, minHeight: 460,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--color-fg-4)', fontSize: 13,
      }}>{t('dashboard.netWorthTrend.noHistory')}</div>
    );
  }

  let series = allSeries;
  if (period === '1Y') series = allSeries.slice(Math.max(0, allSeries.length - 13));
  else if (period === '2Y') series = allSeries.slice(Math.max(0, allSeries.length - 25));
  else if (period === '5Y') series = allSeries.slice(Math.max(0, allSeries.length - 61));

  if (series.length < 2) {
    series = allSeries; // Fallback if data is too short
  }

  const high = Math.max(...series.map(s => s.v));
  const low = Math.min(...series.map(s => s.v));
  const start = series[0].v;
  const end = series[series.length - 1].v;
  const totalGain = end - start;
  const totalGainPct = (totalGain / start) * 100;
  const peak = series.find(s => s.v === high);
  const trough = series.find(s => s.v === low);

  const stats = [
    { key: 'gain', l: t('dashboard.netWorthTrend.gainLabel', { count: series.length - 1 }), v: fmtCNY(totalGain, { signed: true }), c: totalGain >= 0 ? 'var(--color-success)' : 'var(--color-danger)', sub: fmtPct(totalGainPct, { digits: 1 }) },
    { key: 'peak', l: t('dashboard.netWorthTrend.peak'), v: fmtCNY(high), c: 'var(--color-fg-1)', sub: peak?.m ?? '' },
    { key: 'trough', l: t('dashboard.netWorthTrend.trough'), v: fmtCNY(low), c: 'var(--color-fg-1)', sub: trough?.m ?? '' },
    { key: 'volatility', l: t('dashboard.netWorthTrend.volatility'), v: volatility != null ? fmtPct(volatility, { digits: 1, signed: false }) : '—', c: 'var(--color-fg-1)', sub: t('dashboard.netWorthTrend.annualizedSigma') },
  ];

  return (
    <div style={{
      background: 'var(--color-card)', border: '1px solid var(--color-border)',
      borderRadius: 12, boxShadow: 'var(--shadow-sm)', padding: 20,
      display: 'flex', flexDirection: 'column', minHeight: 460,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('dashboard.netWorthTrend.title')}</h3>
          <div style={{ fontSize: 11, color: 'var(--color-fg-4)', marginTop: 2 }}>
            {t('dashboard.netWorthTrend.monthRolling', { count: series.length - 1 })}
          </div>
        </div>
        <div style={{ display: 'inline-flex', gap: 4, padding: 3, background: 'var(--color-border-soft)', borderRadius: 6 }}>
          {PERIOD_ORDER.map((p) => (
            <button key={p} onClick={() => setPeriod(p)} style={{
              border: 'none',
              background: p === period ? 'var(--color-card)' : 'transparent',
              boxShadow: p === period ? 'var(--shadow-sm)' : 'none',
              padding: '3px 10px', borderRadius: 4, fontSize: 10,
              fontFamily: 'var(--font-mono)', fontWeight: 600,
              color: p === period ? 'var(--color-fg-1)' : 'var(--color-fg-4)',
              cursor: 'pointer',
            }}>{t(`dashboard.netWorthTrend.periods.${p}`)}</button>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, marginTop: 12, minHeight: 280 }}>
        <AreaChart data={series} width={780} height={320} />
      </div>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16,
        marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--color-border-soft)',
      }}>
        {stats.map((s) => (
          <div key={s.key}>
            <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--color-fg-4)', letterSpacing: '0.06em' }}>{s.l}</div>
            <div className={s.key === 'volatility' ? '' : 'money-value'} style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 14, color: s.c, marginTop: 4 }}>{s.v}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--color-fg-4)', marginTop: 2 }}>{s.sub}</div>
          </div>
        ))}
      </div>
    </div>
  );
};
