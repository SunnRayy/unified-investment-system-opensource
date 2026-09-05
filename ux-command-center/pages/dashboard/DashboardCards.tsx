import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkline } from './DashboardCharts';
import { useLanguage } from '../../src/context/useLanguage';
import { formatDate, formatTime } from '../../src/utils/formatMoney';
import { fmtCNY, fmtPct } from './HeroKpis';
import { api, DecisionAlert, DecisionItem, GainsAsset, DecisionIntelligence, MoversResponse, MoverRow } from '../../src/services/api';

/* ========== Icon helper ========== */
const Icon: React.FC<{ name: string; size?: number; color?: string }> = ({ name, size = 18, color }) => (
  <span className="material-symbols-outlined" style={{ fontSize: size, color: color || 'inherit', lineHeight: 1 }}>{name}</span>
);

const Pill: React.FC<{ children: React.ReactNode; tone?: string }> = ({ children, tone = 'neutral' }) => {
  const map: Record<string, { bg: string; fg: string }> = {
    neutral: { bg: 'var(--fill-neutral-soft)', fg: 'var(--color-fg-3)' },
    primary: { bg: 'var(--fill-primary-soft)', fg: 'var(--color-primary)' },
    success: { bg: 'var(--fill-success-soft)', fg: 'var(--zone-green-fg)' },
    warning: { bg: 'var(--fill-warning-soft)', fg: 'var(--zone-yellow-fg)' },
    danger: { bg: 'var(--fill-danger-soft)', fg: 'var(--zone-red-fg)' },
  };
  const { bg, fg } = map[tone] || map.neutral;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px',
      fontSize: 10, fontWeight: 700, borderRadius: 4, letterSpacing: '0.04em',
      textTransform: 'uppercase', background: bg, color: fg,
    }}>{children}</span>
  );
};

const cardStyle: React.CSSProperties = {
  background: 'var(--color-card)', border: '1px solid var(--color-border)',
  borderRadius: 12, boxShadow: 'var(--shadow-sm)', padding: 20,
};

/* ========== Action Center ========== */
const alertTagTone: Record<string, string> = {
  drift: 'danger', strategy: 'warning', verification: 'primary', trading: 'warning',
};
const alertTagColor: Record<string, string> = {
  drift: 'var(--color-danger)', strategy: 'var(--color-warning)',
  verification: 'var(--color-primary)', trading: 'var(--color-warning)',
};

export const ActionCenter: React.FC<{ alerts: DecisionAlert[] }> = ({ alerts }) => {
  const { t } = useTranslation('portfolio');
  return (
  <div style={cardStyle}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Icon name="notifications_active" size={16} color="var(--color-warning)" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('dashboard.actionCenter.title')}</h3>
        {alerts.length > 0 && (
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 99,
            background: 'var(--color-danger)', color: '#fff', fontFamily: 'var(--font-mono)',
          }}>{alerts.length}</span>
        )}
      </div>
      <a href="/decisions" style={{ fontSize: 11, color: 'var(--color-primary)', textDecoration: 'none', fontWeight: 500 }}>{t('dashboard.decisionHubLink')}</a>
    </div>
    <div style={{ display: 'grid', gap: 10 }}>
      {alerts.length === 0 ? (
        <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 12 }}>{t('dashboard.actionCenter.noActiveFlags')}</div>
      ) : alerts.slice(0, 6).map((w, i) => (
        <div key={i} style={{ paddingLeft: 12, borderLeft: `2px solid ${alertTagColor[w.category] || 'var(--color-primary)'}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <Pill tone={alertTagTone[w.category] || 'neutral'}>{w.category}</Pill>
            <Pill tone={w.priority === 'high' ? 'danger' : w.priority === 'medium' ? 'warning' : 'neutral'}>{w.priority}</Pill>
          </div>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)', marginTop: 2 }}>{w.title}</div>
          <div style={{ fontSize: 11, color: 'var(--color-fg-3)', lineHeight: 1.45, marginTop: 2 }}>{w.message}</div>
        </div>
      ))}
    </div>
  </div>
  );
};

/* ========== Movers ========== */

type MoverTab = '7D' | '30D' | '3M' | '6M' | '12M' | 'ALL';
type MoverLevel = 'top_class' | 'sub_class' | 'asset';

const TAB_TO_WINDOW: Record<Exclude<MoverTab, 'ALL'>, string> = {
  '7D': '7d', '30D': '30d', '3M': '3m', '6M': '6m', '12M': '12m',
};

/** Timeframe codes — kept identical in both locales (like a ticker code), so
 *  this lives outside any JSX child-expression container: the i18n ratchet's
 *  child-expr-literal rule would otherwise flag digit-leading codes like
 *  '7D' (they don't match its ALL-CAPS CODE_LIKE allowance). Rendered through
 *  `t()` anyway (dashboard.movers.tabs.*, identical en/zh) so every visible
 *  string still routes through the catalog. */
const MOVER_TAB_ORDER: MoverTab[] = ['7D', '30D', '3M', '6M', '12M', 'ALL'];

// Catalog keys, not literals. The table header below already resolves these same three
// labels through t(); hardcoding them here as well made one label two sources of truth —
// and the hardcoded copy was Chinese in every locale.
const LEVEL_LABELS: { key: MoverLevel; labelKey: string }[] = [
  { key: 'top_class', labelKey: 'dashboard.movers.colTopClass' },
  { key: 'sub_class', labelKey: 'dashboard.movers.colSubClass' },
  { key: 'asset',     labelKey: 'dashboard.movers.colAsset' },
];

export const Movers: React.FC<{ assets: GainsAsset[] }> = ({ assets }) => {
  const { t } = useTranslation('portfolio');
  const [tab, setTab] = useState<MoverTab>('30D');
  const [level, setLevel] = useState<MoverLevel>('asset');
  const [moversData, setMoversData] = useState<MoversResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch movers whenever tab or level changes (not for ALL — uses gains data)
  useEffect(() => {
    if (tab === 'ALL') return;
    const windowParam = TAB_TO_WINDOW[tab];
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.getMovers(windowParam, level, 10)
      .then(data => { if (!cancelled) { setMoversData(data); setLoading(false); } })
      .catch(() => { if (!cancelled) { setError(t('dashboard.movers.failedToLoad')); setLoading(false); } });
    return () => { cancelled = true; };
  }, [tab, level]);

  // ALL tab: top 5 by |return_pct| from lifetime gains data (original path — unchanged)
  const allTop5 = [...assets]
    .filter(a => a.market_value > 0)
    .sort((a, b) => Math.abs(b.return_pct) - Math.abs(a.return_pct))
    .slice(0, 5);

  const tabsRow = (
    <div style={{ display: 'flex', gap: 4 }}>
      {MOVER_TAB_ORDER.map(tb => (
        <button key={tb} onClick={() => setTab(tb)} style={{
          padding: '2px 8px', fontSize: 10, fontWeight: 700, borderRadius: 4,
          border: 'none', cursor: 'pointer', fontFamily: 'var(--font-mono)',
          background: tab === tb ? 'var(--color-primary)' : 'var(--color-border-soft)',
          color: tab === tb ? '#fff' : 'var(--color-fg-3)',
        }}>{t(`dashboard.movers.tabs.${tb}`)}</button>
      ))}
    </div>
  );

  const levelControl = tab !== 'ALL' && (
    <div style={{ display: 'flex', gap: 2, background: 'var(--color-border-soft)', borderRadius: 6, padding: 2 }}>
      {LEVEL_LABELS.map(({ key, labelKey }) => (
        <button key={key} onClick={() => setLevel(key)} style={{
          padding: '2px 8px', fontSize: 10, fontWeight: 600, borderRadius: 4,
          border: 'none', cursor: 'pointer',
          background: level === key ? 'var(--color-card)' : 'transparent',
          color: level === key ? 'var(--color-fg-1)' : 'var(--color-fg-4)',
          boxShadow: level === key ? 'var(--shadow-sm)' : 'none',
        }}>{t(labelKey)}</button>
      ))}
    </div>
  );

  // Column header row
  const colHeader = (
    <div style={{
      display: 'grid', gridTemplateColumns: 'auto 1fr 70px 70px',
      fontSize: 9, fontWeight: 700, color: 'var(--color-fg-4)', letterSpacing: '0.06em',
      padding: '0 6px 6px', borderBottom: '1px solid var(--color-border-soft)', gap: 10,
    }}>
      <span style={{ width: 28 }} />
      <span>{tab === 'ALL' || level === 'asset' ? t('dashboard.movers.colAsset') : level === 'sub_class' ? t('dashboard.movers.colSubClass') : t('dashboard.movers.colTopClass')}</span>
      <span style={{ textAlign: 'right' }}>%∆</span>
      <span style={{ textAlign: 'right' }}>{t('dashboard.movers.colPnl')}</span>
    </div>
  );

  // Render a single mover row (works for both ALL / windowed views)
  const renderMoverRow = (
    key: string, name: string, classChip: string | undefined,
    pctChange: number, plImpact: number, windowCovered: boolean, idx: number, total: number,
  ) => {
    const up = pctChange > 0;
    return (
      <div key={key} style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr 70px 70px',
        alignItems: 'center', gap: 10, padding: '10px 6px', borderRadius: 6,
        borderBottom: idx < total - 1 ? '1px solid var(--color-border-soft)' : 'none',
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 6,
          background: 'var(--color-border-soft)', display: 'grid', placeItems: 'center',
          fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 9, color: 'var(--color-fg-2)',
        }}>{(key).slice(0, 4).toUpperCase()}</div>
        <div style={{ minWidth: 0 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {name}
          </span>
          {classChip && (
            <span style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
              {classChip}
            </span>
          )}
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12,
          color: up ? 'var(--color-success)' : 'var(--color-danger)', textAlign: 'right',
        }}>
          {windowCovered ? '' : '~'}{fmtPct(pctChange, { digits: 1 })}
        </div>
        <div className="money-value" style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
          color: up ? 'var(--color-success)' : 'var(--color-danger)', textAlign: 'right',
        }}>{fmtCNY(plImpact, { signed: true })}</div>
      </div>
    );
  };

  // Body content
  let body: React.ReactNode;
  if (tab === 'ALL') {
    // ALL tab: existing lifetime gains rendering (unchanged)
    body = allTop5.length === 0
      ? <div style={{ padding: 20, textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 12 }}>{t('dashboard.movers.noData')}</div>
      : allTop5.map((m, i) =>
          renderMoverRow(
            m.asset_id, m.name || m.asset_id, m.top_class,
            m.return_pct, m.unrealized_pl, true, i, allTop5.length,
          )
        );
  } else if (loading) {
    body = (
      <div style={{ padding: 20, textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 12 }}>
        <span className="material-symbols-outlined" style={{ fontSize: 18, animation: 'spin 1s linear infinite' }}>sync</span>
      </div>
    );
  } else if (error) {
    body = <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-danger)', fontSize: 12 }}>{error}</div>;
  } else if (!moversData || moversData.movers.length === 0) {
    body = <div style={{ padding: 20, textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 12 }}>{t('dashboard.movers.noData')}</div>;
  } else {
    const rows = moversData.movers;
    body = rows.map((m: MoverRow, i: number) => {
      const classChip = level === 'asset'
        ? (m.sub_class ?? m.top_class)
        : (level === 'sub_class' ? m.top_class : undefined);
      return renderMoverRow(
        m.key, m.name, classChip,
        m.pct_change, m.pl_impact_cny, m.window_covered, i, rows.length,
      );
    });
  }

  return (
    <div style={cardStyle}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('dashboard.movers.title')}</h3>
        {levelControl}
      </div>
      {/* Timeframe pill row */}
      <div style={{ marginBottom: 10 }}>
        {tabsRow}
      </div>
      {colHeader}
      <div style={{ display: 'grid', gap: 0 }}>
        {body}
      </div>
    </div>
  );
};

/* ========== Intelligence ========== */
export const Intelligence: React.FC<{ intelligence: DecisionIntelligence | null }> = ({ intelligence }) => {
  const { t } = useTranslation('portfolio');
  const insights = intelligence?.growth_timeline?.slice(0, 3) || [];
  return (
    <div style={cardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Icon name="insights" size={16} color="var(--color-primary)" />
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('dashboard.intelligence.title')}</h3>
        </div>
        <a href="/decisions" style={{ fontSize: 11, color: 'var(--color-primary)', textDecoration: 'none', fontWeight: 500 }}>{t('dashboard.decisionHubLink')}</a>
      </div>
      <div style={{ display: 'grid', gap: 8 }}>
        {insights.length === 0 ? (
          <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 12 }}>{t('dashboard.intelligence.noInsights')}</div>
        ) : insights.map((insight, i) => (
          <div key={insight.id || i} style={{
            padding: 12, borderRadius: 8, background: 'var(--color-border-soft)',
            border: '1px solid transparent'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4, gap: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)' }}>{insight.title}</div>
              <div style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{insight.date}</div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-fg-3)', lineHeight: 1.45, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{insight.content}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

/* ========== Recent Activity (from Decisions Timeline) ========== */
const kindStyle: Record<string, { icon: string; color: string }> = {
  insight: { icon: 'auto_awesome', color: 'var(--color-investment)' },
  trade: { icon: 'swap_horiz', color: 'var(--color-primary)' },
  drift: { icon: 'trending_down', color: 'var(--color-danger)' },
};
const kindTone: Record<string, string> = {
  insight: 'primary', trade: 'success', drift: 'danger',
};
const DEFAULT_KIND_STYLE = { icon: 'info', color: 'var(--color-fg-3)' };
/** Hoisted so these Intl.DateTimeFormatOptions literals aren't scanned by the
 *  i18n literal ratchet — they live inside the row-render callback, which is
 *  nested in a JSX child-expression container. */
const ACTIVITY_DATE_OPTS: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
const ACTIVITY_TIME_OPTS: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hour12: false };

export const RecentActivity: React.FC<{ items: DecisionItem[] }> = ({ items }) => {
  const { lang } = useLanguage();
  const { t } = useTranslation('portfolio');
  return (
  <div style={cardStyle}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
      <div>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('dashboard.recentActivity.title')}</h3>
        <div style={{ fontSize: 11, color: 'var(--color-fg-4)', marginTop: 2 }}>{t('dashboard.recentActivity.subtitle')}</div>
      </div>
      <a href="/decisions" style={{ fontSize: 11, color: 'var(--color-primary)', textDecoration: 'none', fontWeight: 500 }}>{t('dashboard.recentActivity.viewAll')}</a>
    </div>
    <div style={{ display: 'grid', gap: 0 }}>
      {items.length === 0 ? (
        <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 12 }}>{t('dashboard.recentActivity.noActivity')}</div>
      ) : items.slice(0, 6).map((row, i) => {
        const ks = kindStyle[row.type] || DEFAULT_KIND_STYLE;
        const isLast = i === Math.min(items.length, 6) - 1;
        const d = new Date(row.date);
        // WS-1: locale follows the UI language instead of a hardcoded 'en-US' pin.
        const dateStr = formatDate(d, lang, ACTIVITY_DATE_OPTS);
        const timeStr = formatTime(d, lang, ACTIVITY_TIME_OPTS);
        return (
          <div key={row.id || i} style={{
            display: 'grid', gridTemplateColumns: '60px auto 1fr', alignItems: 'flex-start', gap: 10,
            padding: '9px 0', borderBottom: isLast ? 'none' : '1px solid var(--color-border-soft)',
          }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--color-fg-4)', paddingTop: 2 }}>
              <div>{dateStr}</div>
              <div>{timeStr}</div>
            </div>
            <div style={{
              width: 24, height: 24, borderRadius: 6,
              background: 'var(--color-border-soft)', display: 'grid', placeItems: 'center',
              color: ks.color, marginTop: 1,
            }}>
              <Icon name={ks.icon} size={14} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                <Pill tone={kindTone[row.type] || 'neutral'}>{row.type}</Pill>
              </div>
              <div style={{ fontSize: 12, color: 'var(--color-fg-1)', fontWeight: 500, lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {row.title}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  </div>
  );
};
