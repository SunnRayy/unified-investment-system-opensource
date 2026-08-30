import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Donut, DriftBar } from './DashboardCharts';
import { fmtPct } from './HeroKpis';
import { AllocationRow } from '../../src/services/api';

const ALLOC_COLORS = [
  '#94a3b8', '#1e293b', '#135bec', '#60a5fa', '#cbd5e1',
  '#64748b', '#3b82f6', '#0f172a', '#475569', '#a3b8d4',
  '#334155', '#6366f1', '#059669', '#dc2626',
];

/** Hoisted out of the row-render callback (which lives inside a JSX child-
 *  expression container) so these CSS custom-property names aren't scanned
 *  by the i18n literal ratchet as if they were prose. */
function getDriftColor(drift: number): string {
  const overTol = Math.abs(drift) >= 5;
  if (overTol) return 'var(--color-danger)';
  return Math.abs(drift) >= 2 ? 'var(--color-warning)' : 'var(--color-fg-4)';
}

interface Props {
  compassData: AllocationRow[];
}

export const AllocationCard: React.FC<Props> = ({ compassData }) => {
  const { t } = useTranslation('portfolio');
  const [view, setView] = useState<'top' | 'sub'>('top');
  const rows = compassData.filter(r => view === 'top' ? r.is_top_level : !r.is_top_level);
  const unitLabel = view === 'top' ? t('dashboard.allocation.classesUnit') : t('dashboard.allocation.subClassesUnit');
  const viewOptions = [
    { k: 'top' as const, l: t('dashboard.allocation.topClass') },
    { k: 'sub' as const, l: t('dashboard.allocation.subClass') },
  ];

  const donutData = rows.map((r, i) => ({
    cur: r.current_value,
    color: ALLOC_COLORS[i % ALLOC_COLORS.length],
    name: r.asset_class,
  }));
  const total = rows.reduce((s, r) => s + r.current_value, 0);

  return (
    <div style={{
      background: 'var(--color-card)', border: '1px solid var(--color-border)',
      borderRadius: 12, boxShadow: 'var(--shadow-sm)', padding: 20,
      display: 'flex', flexDirection: 'column', minHeight: 320,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, gap: 12 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('dashboard.allocation.title')}</h3>
          <div style={{ fontSize: 11, color: 'var(--color-fg-4)', marginTop: 2 }}>
            {t('dashboard.allocation.summary', { count: rows.length, unit: unitLabel })}
          </div>
        </div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <div style={{ display: 'inline-flex', gap: 2, padding: 3, background: 'var(--color-border-soft)', borderRadius: 6 }}>
            {viewOptions.map((o) => (
              <button key={o.k} onClick={() => setView(o.k)} style={{
                border: 'none',
                background: view === o.k ? 'var(--color-card)' : 'transparent',
                boxShadow: view === o.k ? 'var(--shadow-sm)' : 'none',
                padding: '3px 10px', borderRadius: 4, fontSize: 10,
                fontWeight: 600, color: view === o.k ? 'var(--color-fg-1)' : 'var(--color-fg-4)',
                cursor: 'pointer', letterSpacing: '0.02em',
              }}>{o.l}</button>
            ))}
          </div>
          <a href="/compass" style={{ fontSize: 11, color: 'var(--color-primary)', textDecoration: 'none', fontWeight: 500 }}>{t('dashboard.allocation.openCompass')}</a>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 20, alignItems: 'center', flex: 1 }}>
        <div style={{ position: 'relative', width: 180, height: 180 }}>
          <Donut data={donutData} size={180} thickness={20} />
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center' }}>
            <div>
              <div style={{ fontSize: 10, color: 'var(--color-fg-4)', fontWeight: 700, letterSpacing: '0.06em' }}>{t('dashboard.allocation.total')}</div>
              <div className="money-value" style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 16 }}>
                ¥{(total / 1_000_000).toFixed(2)}M
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--color-fg-4)', marginTop: 2 }}>
                {rows.length} {unitLabel}
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gap: 5, fontSize: 11, maxHeight: 340, overflowY: 'auto' }}>
          {rows.map((a, i) => {
            const drift = a.drift_pct;
            const driftColor = getDriftColor(drift);
            const color = ALLOC_COLORS[i % ALLOC_COLORS.length];
            return (
              <div key={a.asset_class} style={{
                display: 'grid', gridTemplateColumns: '10px 1fr 50px 120px 60px',
                alignItems: 'center', gap: 10, padding: '3px 6px', borderRadius: 4,
                fontFamily: 'var(--font-mono)',
              }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
                <span style={{ fontFamily: 'var(--font-sans)', color: 'var(--color-fg-2)', fontSize: 12 }}>{a.asset_class}</span>
                <span style={{ textAlign: 'right', color: 'var(--color-fg-3)', fontSize: 11 }}>{a.current_pct.toFixed(1)}%</span>
                <DriftBar pct={a.current_pct} target={a.target_pct} color={color} />
                <span style={{ textAlign: 'right', color: driftColor, fontSize: 11, fontWeight: 600 }}>{fmtPct(drift, { digits: 1 })}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{
        display: 'flex', gap: 16, marginTop: 'auto', paddingTop: 12,
        borderTop: '1px solid var(--color-border-soft)',
        fontSize: 11, color: 'var(--color-fg-3)', fontFamily: 'var(--font-mono)',
      }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 8, height: 2, background: 'var(--color-fg-2)' }} /> {t('dashboard.allocation.legendTarget')}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 8, height: 4, background: 'var(--color-primary)' }} /> {t('dashboard.allocation.legendCurrent')}
        </span>
        <span style={{ marginLeft: 'auto', color: 'var(--color-fg-4)' }}>{t('dashboard.allocation.tolerance')}</span>
      </div>
    </div>
  );
};
