import React, { useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { useLanguage } from '../src/context/useLanguage';
import { formatDate } from '../src/utils/formatMoney';
import { createAuthSSE } from '../src/services/authFetch';
import {
  api, KPI, PerformanceReturns, AllocationRow, HistoryItem,
  GainsAsset, GainsResponse, DecisionAlert, DecisionItem, DecisionTimeline,
  DecisionIntelligence,
  SentimentAPI, SentimentIndicator, MarketStatusComposite, CashFlowAnalysis,
  AuditSummary, ExportAPI, AnalyticsAPI
} from '../src/services/api';
import { HeroKpis } from './dashboard/HeroKpis';
import { AllocationCard } from './dashboard/AllocationCard';
import { NetWorthTrend } from './dashboard/NetWorthTrend';
import { ActionCenter, Movers, Intelligence, RecentActivity } from './dashboard/DashboardCards';

function computeMarketStatus(indicators: SentimentIndicator[]): MarketStatusComposite {
  const colorToScore: Record<string, number> = { green: 2, 'light-green': 1, yellow: 0, orange: -1, red: -2 };
  const sectionScores: Record<string, number[]> = { equity_macro: [], gold: [], crypto: [] };
  let redCount = 0;
  for (const ind of indicators) {
    const score = colorToScore[ind.zone_color] ?? 0;
    if (ind.zone_color === 'red') redCount++;
    if (sectionScores[ind.section]) sectionScores[ind.section].push(score);
  }
  const avg = (arr: number[]) => arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  const sectionAvgs = { equity_macro: avg(sectionScores.equity_macro), gold: avg(sectionScores.gold), crypto: avg(sectionScores.crypto) };
  const validAvgs = Object.values(sectionAvgs).filter((v): v is number => v !== null);
  const composite = validAvgs.length > 0 ? validAvgs.reduce((a, b) => a + b, 0) / validAvgs.length : 0;
  let verdict: string, verdict_color: string;
  if (composite > 0.8) { verdict = 'Favorable'; verdict_color = 'green'; }
  else if (composite > 0) { verdict = 'Neutral'; verdict_color = 'yellow'; }
  else if (composite > -0.8) { verdict = 'Cautious'; verdict_color = 'orange'; }
  else { verdict = 'Risk-Off'; verdict_color = 'red'; }
  return { verdict, verdict_color, score: Math.round(composite * 100) / 100, red_count: redCount, total_count: indicators.length, sections: sectionAvgs };
}

/* ========== Sync Split Button ========== */
const SyncSplit: React.FC<{ onSyncAll: () => void; syncing: boolean; onToast: (msg: string, type: 'success' | 'error') => void }> = ({ onSyncAll, syncing, onToast }) => {
  const { t } = useTranslation('common');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const items = [
    { icon: 'folder_open', label: t('dashboard.syncFiles'), action: () => { onSyncAll(); setOpen(false); } },
    { icon: 'price_check', label: t('dashboard.refreshPrices'), action: async () => { try { const r = await api.triggerValuationRefresh(); onToast(t('dashboard.pricesRefreshed', { count: r.refreshed_count }), 'success'); } catch (e: any) { onToast(e?.message || t('dashboard.priceRefreshFailed'), 'error'); } setOpen(false); } },
    { icon: 'public', label: t('dashboard.refreshMarkets'), action: async () => { try { await SentimentAPI.refresh(); onToast(t('dashboard.marketRefreshed'), 'success'); } catch (e: any) { onToast(e?.message || t('dashboard.marketRefreshFailed'), 'error'); } setOpen(false); } },
  ];

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div style={{ display: 'inline-flex', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-card)' }}>
        <button onClick={onSyncAll} disabled={syncing} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 10px 6px 12px',
          fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 500,
          border: 'none', background: 'transparent', color: 'var(--color-fg-2)', cursor: syncing ? 'wait' : 'pointer',
          borderTopLeftRadius: 7, borderBottomLeftRadius: 7,
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 14, animation: syncing ? 'spin 1s linear infinite' : 'none' }}>sync</span>
          {syncing ? t('dashboard.syncingEllipsis') : t('dashboard.syncAll')}
        </button>
        <button onClick={() => setOpen(o => !o)} style={{
          padding: '6px 8px', border: 'none', borderLeft: '1px solid var(--color-border)',
          background: 'transparent', color: 'var(--color-fg-3)', cursor: 'pointer',
          borderTopRightRadius: 7, borderBottomRightRadius: 7,
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>{open ? 'expand_less' : 'expand_more'}</span>
        </button>
      </div>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', right: 0, width: 220,
          background: 'var(--color-card)', border: '1px solid var(--color-border)',
          borderRadius: 8, boxShadow: 'var(--shadow-lg)', padding: 4, zIndex: 50,
        }}>
          {items.map((it) => (
            <button key={it.label} onClick={it.action} style={{
              display: 'grid', gridTemplateColumns: 'auto 1fr', alignItems: 'center', gap: 10,
              width: '100%', padding: '8px 10px', border: 'none', background: 'transparent',
              borderRadius: 6, cursor: 'pointer', textAlign: 'left',
            }}>
              <span className="material-symbols-outlined" style={{ fontSize: 16, color: 'var(--color-fg-3)' }}>{it.icon}</span>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)' }}>{it.label}</span>
            </button>
          ))}
          <div style={{ borderTop: '1px solid var(--color-border-soft)', margin: '4px 0' }} />
          <button onClick={() => { onSyncAll(); setOpen(false); }} style={{
            display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px',
            border: 'none', background: 'transparent', borderRadius: 6, cursor: 'pointer',
            color: 'var(--color-primary)', fontSize: 12, fontWeight: 600,
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>sync</span> {t('dashboard.syncAll')}
          </button>
        </div>
      )}
    </div>
  );
};

export const Dashboard: React.FC = () => {
  const { t } = useTranslation('common');
  const { includeNonRebalanceable } = usePortfolioFilter();
  const { lang } = useLanguage();
  const [kpi, setKpi] = useState<KPI | null>(null);
  const [perfReturns, setPerfReturns] = useState<PerformanceReturns | null>(null);
  const [marketStatus, setMarketStatus] = useState<MarketStatusComposite | null>(null);
  const [cashFlow, setCashFlow] = useState<CashFlowAnalysis | null>(null);
  const [compassData, setCompassData] = useState<AllocationRow[]>([]);
  const [performanceHistory, setPerformanceHistory] = useState<HistoryItem[]>([]);
  const [gainsAssets, setGainsAssets] = useState<GainsAsset[]>([]);
  const [alerts, setAlerts] = useState<DecisionAlert[]>([]);
  const [intelligence, setIntelligence] = useState<DecisionIntelligence | null>(null);
  const [recentItems, setRecentItems] = useState<DecisionItem[]>([]);
  const [filterDataError, setFilterDataError] = useState<string | null>(null);
  const [auditSummary, setAuditSummary] = useState<AuditSummary | null>(null);
  const [macro, setMacro] = useState<{ usd_cny?: number | null; source?: string; fallback_used?: boolean } | null>(null);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [syncStatus, setSyncStatus] = useState<'IDLE' | 'SYNCING' | 'DONE'>('IDLE');
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogModal, setShowLogModal] = useState(false);

  // Load static data once
  useEffect(() => {
    (async () => {
      const results = await Promise.allSettled([
        api.getAuditSummary(),
        api.getDecisionsIntelligence().catch(() => null),
        SentimentAPI.getCached().catch(() => null),
        AnalyticsAPI.getCashFlowTrends().catch(() => null),
        api.getDecisionAlerts().catch(() => ({ alerts: [], counts: { high: 0, medium: 0, low: 0 } })),
        api.getDecisionsTimeline(10, 'all').catch(() => ({ items: [], summary: { total: 0, adopted: 0, pending: 0 } })),
        api.getValuationMacro(),
      ]);
      if (results[0].status === 'fulfilled') setAuditSummary(results[0].value);
      if (results[1].status === 'fulfilled') setIntelligence(results[1].value);
      if (results[2].status === 'fulfilled' && results[2].value?.indicators?.length > 0) {
        setMarketStatus(computeMarketStatus(results[2].value.indicators));
      }
      if (results[3].status === 'fulfilled' && results[3].value) setCashFlow(results[3].value);
      if (results[4].status === 'fulfilled') setAlerts((results[4].value as any).alerts || []);
      if (results[5].status === 'fulfilled') setRecentItems((results[5].value as DecisionTimeline).items || []);
      if (results[6].status === 'fulfilled') setMacro(results[6].value);
    })();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // Load toggle-dependent data
  useEffect(() => { loadFilteredData(); }, [includeNonRebalanceable]);

  const loadFilteredData = async () => {
    setFilterDataError(null);
    const results = await Promise.allSettled([
      api.getKPI(includeNonRebalanceable),
      api.getCompassAllocation(includeNonRebalanceable),
      api.getPerformanceHistory('all_time', false, includeNonRebalanceable),
      api.getReturns('all_time', false, includeNonRebalanceable),
      api.getGainsAnalysis('all_time', false, includeNonRebalanceable).catch(() => null),
    ]);
    if (results[0].status === 'fulfilled') setKpi(results[0].value);
    if (results[1].status === 'fulfilled') setCompassData(results[1].value.rows);
    else if (results[1].status === 'rejected') {
      console.error("getCompassAllocation failed:", results[1].reason);
      setFilterDataError(t('dashboard.filterDataError'));
    }
    if (results[2].status === 'fulfilled') setPerformanceHistory(results[2].value);
    if (results[3].status === 'fulfilled') setPerfReturns(results[3].value);
    if (results[4].status === 'fulfilled' && results[4].value) setGainsAssets((results[4].value as GainsResponse).assets || []);
  };

  const handleSync = async () => {
    setSyncStatus('SYNCING'); setLogs([]); setShowLogModal(true);
    try {
      await api.startSync();
      const evtSource = await createAuthSSE('/api/sync/stream');
      evtSource.addEventListener('log', (e: MessageEvent) => setLogs(prev => [...prev, e.data]));
      evtSource.addEventListener('end', () => { evtSource.close(); setSyncStatus('DONE'); loadFilteredData(); });
      evtSource.onerror = () => { setLogs(prev => [...prev, `ERROR: ${t('dashboard.streamConnectionLost')}`]); evtSource.close(); setSyncStatus('DONE'); };
    } catch (e) { setLogs(prev => [...prev, `ERROR: ${e}`]); setSyncStatus('DONE'); }
  };

  const formatLastSync = (ts: string | null) => {
    if (!ts) return t('dashboard.never');
    const diffMs = Date.now() - new Date(ts).getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 60) return t('dashboard.minutesAgo', { count: diffMins });
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return t('dashboard.hoursAgo', { count: diffHours });
    return formatDate(ts, lang);
  };

  // Compute vs-last-month from performance history
  const sorted = [...performanceHistory].sort((a, b) => new Date(a.name).getTime() - new Date(b.name).getTime());
  const baseline = sorted.length >= 2 ? sorted[sorted.length - 2] : null;
  const delta30d = kpi && baseline ? kpi.net_worth - baseline.value : null;
  const pct30d = delta30d !== null && baseline && baseline.value > 0 ? (delta30d / baseline.value) * 100 : null;
  const sinceLabel = baseline ? formatDate(baseline.name, lang, { month: 'short', year: 'numeric' }) : '—';
  const sparkData = sorted.slice(-30).map(h => h.value);

  return (
    <div data-testid="dashboard-page" style={{ minHeight: '100vh', background: 'var(--color-bg)', color: 'var(--color-fg-1)' }}>
      {filterDataError && (
        <div style={{ background: '#fef2f2', color: '#b91c1c', padding: '8px 32px', fontSize: 13, borderBottom: '1px solid #fecaca' }}>
          ⚠ {filterDataError}
        </div>
      )}
      {toast && (
        <div style={{
          position: 'fixed', top: 72, right: 16, zIndex: 100,
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 16px', borderRadius: 10,
          background: 'var(--color-card)',
          border: `1px solid ${toast.type === 'success' ? 'var(--color-success)' : 'var(--color-danger)'}`,
          color: toast.type === 'success' ? 'var(--color-success)' : 'var(--color-danger)',
          fontSize: 12, fontWeight: 600,
          boxShadow: 'var(--shadow-lg)',
        }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
            {toast.type === 'success' ? 'check_circle' : 'error'}
          </span>
          {toast.message}
          <button onClick={() => setToast(null)} style={{
            background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginLeft: 8,
            color: 'inherit', opacity: 0.6,
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
          </button>
        </div>
      )}
      {/* Header */}
      <header style={{
        height: 64, flexShrink: 0, borderBottom: '1px solid var(--color-border)',
        background: 'color-mix(in srgb, var(--color-bg) 80%, transparent)',
        backdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', padding: '0 32px', position: 'sticky', top: 0, zIndex: 5,
      }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 14 }}>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, letterSpacing: '-0.01em' }}>{t('nav.unifiedPortfolio')}</h1>
          {auditSummary && (
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--color-fg-3)',
              paddingLeft: 14, borderLeft: '1px solid var(--color-border)',
            }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--color-success)', animation: 'uis-pulse 1.6s ease-out infinite', display: 'inline-block' }} />
              <span style={{ fontFamily: 'var(--font-mono)' }}>{t('dashboard.lastSynced', { time: formatLastSync(auditSummary.last_sync_timestamp) })}</span>
            </div>
          )}
        </div>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <button onClick={() => ExportAPI.downloadAiContext()} style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px',
            fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 500, borderRadius: 8,
            border: '1px solid var(--color-border)', background: 'var(--color-card)',
            color: 'var(--color-fg-2)', cursor: 'pointer',
          }}>
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>bolt</span>
            {t('dashboard.exportAiContext')}
          </button>
          <SyncSplit onSyncAll={handleSync} syncing={syncStatus === 'SYNCING'} onToast={(msg, type) => setToast({ message: msg, type })} />
        </div>
      </header>

      {/* Sync Terminal Modal */}
      {showLogModal && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)', padding: 32 }}>
          <div style={{ background: '#0f172a', borderRadius: 12, width: '100%', maxWidth: 800, height: 500, display: 'flex', flexDirection: 'column', border: '1px solid #334155' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 16, borderBottom: '1px solid #334155' }}>
              <span style={{ color: '#fff', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700 }}>{t('dashboard.syncTerminal')}</span>
              <button onClick={() => setShowLogModal(false)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: 16, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              {logs.map((log, i) => (
                <div key={i} style={{ color: log.includes('ERROR') ? '#ef4444' : log.includes('WARNING') ? '#eab308' : '#cbd5e1', marginBottom: 2, wordBreak: 'break-word' }}>{log}</div>
              ))}
              {syncStatus === 'SYNCING' && <div style={{ color: '#3b82f6', animation: 'uis-pulse 1s ease-out infinite' }}>_</div>}
            </div>
          </div>
        </div>
      )}

      {/* Main content — Dense layout */}
      <main style={{ padding: '20px 28px 32px', display: 'grid', gap: 16 }}>
        <HeroKpis
          kpi={kpi} perfReturns={perfReturns} marketStatus={marketStatus}
          cashFlow={cashFlow} sparkData={sparkData} delta30d={delta30d}
          pct30d={pct30d} sinceLabel={sinceLabel} macro={macro}
        />
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 16, alignItems: 'start' }}>
          {/* Left Column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <NetWorthTrend performanceHistory={performanceHistory} />
            <AllocationCard compassData={compassData} />
            <Movers assets={gainsAssets} />
          </div>
          {/* Right Column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <ActionCenter alerts={alerts} />
            <Intelligence intelligence={intelligence} />
            <RecentActivity items={recentItems} />
          </div>
        </div>
      </main>
    </div>
  );
};
