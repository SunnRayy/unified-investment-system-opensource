import React, { useEffect, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AssetCaseFile as AssetCaseFileModel, OperationsAPI } from '../src/services/api';
import {
  ActionBtn,
  Card,
  ColDef,
  OpsTable,
  Pill,
  Section,
  StatusPill,
} from '../components/operations';
import { useLanguage } from '../src/context/useLanguage';
import { localizedClassName } from '../src/utils/localizedClassName';

interface CaseIndexItem {
  asset_id: string;
  display_name: string;
  class_name: string;
  class_name_cn: string | null | undefined;
  signal: string;
  severity: 'high' | 'review' | 'low';
}

type TraceEvent = AssetCaseFileModel['source_trace'][number];

const traceEventType = (ev: Record<string, unknown>): 'Transaction' | 'Sync Run' | 'Snapshot' => {
  const t = String(ev.evidence_type || '').toLowerCase();
  if (t.includes('transaction')) return 'Transaction';
  if (t.includes('run') || t.includes('sync')) return 'Sync Run';
  return 'Snapshot';
};

const traceTypeTone: Record<string, 'primary' | 'neutral' | 'warning'> = {
  Transaction: 'primary',
  'Sync Run': 'warning',
  Snapshot: 'neutral',
};

const signalHealthy = (s: string) => /no anomalies|healthy/i.test(s);

const traceTypeLabel = (kind: 'Transaction' | 'Sync Run' | 'Snapshot', t: TFunction): string => {
  switch (kind) {
    case 'Transaction': return t('assetCaseFile.traceType.transaction');
    case 'Sync Run': return t('assetCaseFile.traceType.syncRun');
    case 'Snapshot': return t('assetCaseFile.traceType.snapshot');
  }
};

const getTraceCols = (t: TFunction): ColDef<TraceEvent>[] => [
  {
    label: t('assetCaseFile.traceCols.type'),
    render: (r) => {
      const kind = traceEventType(r as unknown as Record<string, unknown>);
      return <Pill tone={traceTypeTone[kind]}>{traceTypeLabel(kind, t)}</Pill>;
    },
    width: 110,
  },
  { label: t('assetCaseFile.traceCols.description'), key: 'description' },
  { label: t('assetCaseFile.traceCols.source'), key: 'source_system', mono: true, width: 160 },
  {
    label: t('assetCaseFile.traceCols.timestamp'), align: 'right', mono: true, width: 150,
    render: (r) => r.timestamp ? String(r.timestamp).slice(0, 16).replace('T', ' ') : '—',
  },
];

export const AssetCaseFile: React.FC = () => {
  const { t } = useTranslation('operations');
  const { lang } = useLanguage();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const assetId = searchParams.get('asset_id') || '';

  const [caseIndex, setCaseIndex] = useState<CaseIndexItem[]>([]);
  const [indexLoading, setIndexLoading] = useState(true);
  const [caseFile, setCaseFile] = useState<AssetCaseFileModel | null>(null);
  const [caseLoading, setCaseLoading] = useState(false);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [caseError, setCaseError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  // Build case navigator from portfolio audit
  useEffect(() => {
    const loadCaseIndex = async () => {
      setIndexLoading(true);
      setIndexError(null);
      try {
        const summary = await OperationsAPI.getPortfolioAudit();
        const classes = summary.asset_classes
          .filter((r) => r.open_case_count > 0)
          .map((r) => r.class_name);
        const investigations = await Promise.all(
          classes.map(async (c) => {
            try { return await OperationsAPI.getAssetClassAudit(c); }
            catch { return null; }
          })
        );
        const dedup = new Map<string, CaseIndexItem>();
        investigations.forEach((inv) => {
          if (!inv) return;
          inv.groups.forEach((g) => {
            if (g.group_type === 'derived_secondary') return;
            g.assets.forEach((a) => {
              if (a.asset_id === 'AIA' || a.asset_id === 'trade_logs') return;
              if (dedup.has(a.asset_id)) return;
              dedup.set(a.asset_id, {
                asset_id: a.asset_id,
                display_name: a.display_name || a.asset_id,
                class_name: inv.class_name,
                class_name_cn: inv.class_name_cn,
                signal: a.primary_signal,
                severity: a.status === 'warning' ? 'high' : a.status === 'review' ? 'review' : 'low',
              });
            });
          });
        });
        setCaseIndex(Array.from(dedup.values()).slice(0, 20));
      } catch (err) {
        console.error(err);
        setIndexError(t('assetCaseFile.errors.index'));
      } finally {
        setIndexLoading(false);
      }
    };
    loadCaseIndex();
  }, []);

  // Auto-select first case
  useEffect(() => {
    if (assetId || caseIndex.length === 0) return;
    setSearchParams({ asset_id: caseIndex[0].asset_id }, { replace: true });
  }, [assetId, caseIndex, setSearchParams]);

  // Load selected case file
  useEffect(() => {
    const loadCase = async () => {
      if (!assetId) { setCaseFile(null); return; }
      setCaseLoading(true);
      setCaseError(null);
      try {
        const payload = await OperationsAPI.getAssetCaseFile(assetId);
        setCaseFile(payload);
      } catch (err) {
        console.error(err);
        setCaseError(t('assetCaseFile.errors.caseFile'));
      } finally {
        setCaseLoading(false);
      }
    };
    loadCase();
  }, [assetId]);

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Header */}
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
            <span className="uis-eyebrow" style={{ fontSize: 10 }}>{t('assetCaseFile.breadcrumb')}</span>
          </div>
          <div style={{ padding: '18px 20px' }}>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
              {t('assetCaseFile.title')}
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
              {t('assetCaseFile.subtitle')}
            </p>
          </div>
        </Card>

        {/* Two-column layout */}
        <div style={{ display: 'grid', gridTemplateColumns: '280px minmax(0,1fr)', gap: 16, alignItems: 'start' }}>

          {/* Case Navigator sidebar */}
          <Card>
            <Section icon="list_alt" title={t('assetCaseFile.caseNavigator')}>
              {/* Asset ID lookup — works even when no flagged cases exist */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const q = searchInput.trim();
                  if (q) setSearchParams({ asset_id: q });
                }}
                style={{ display: 'flex', gap: 6, marginBottom: 12 }}
              >
                <input
                  ref={searchRef}
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  placeholder={t('assetCaseFile.lookupPlaceholder')}
                  style={{
                    flex: 1, padding: '6px 10px', fontSize: 12, borderRadius: 7,
                    border: '1px solid var(--color-border)', background: 'var(--color-bg)',
                    color: 'var(--color-fg-1)', fontFamily: 'var(--font-mono)',
                    outline: 'none',
                  }}
                />
                <button
                  type="submit"
                  style={{
                    padding: '6px 10px', borderRadius: 7, fontSize: 12, fontWeight: 600,
                    background: 'var(--color-primary)', color: '#fff', border: 'none',
                    cursor: 'pointer',
                  }}
                >
                  {t('assetCaseFile.go')}
                </button>
              </form>

              {indexLoading && (
                <div style={{ fontSize: 12, color: 'var(--color-fg-4)', padding: '4px 0' }}>
                  {t('assetCaseFile.loadingFlaggedCases')}
                </div>
              )}
              {indexError && (
                <div style={{ fontSize: 12, color: 'var(--color-danger)', padding: '8px 0' }}>{indexError}</div>
              )}
              {!indexLoading && !indexError && caseIndex.length === 0 && (
                <div style={{ fontSize: 11, color: 'var(--color-fg-4)', padding: '6px 0', lineHeight: 1.6 }}>
                  {t('assetCaseFile.noFlaggedCases')}
                </div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: caseIndex.length > 0 ? 4 : 0 }}>
                {caseIndex.map((item) => {
                  const active = assetId === item.asset_id;
                  return (
                    <button
                      key={item.asset_id}
                      onClick={() => setSearchParams({ asset_id: item.asset_id })}
                      style={{
                        width: '100%', textAlign: 'left', cursor: 'pointer',
                        padding: '10px 12px', borderRadius: 8,
                        border: `1px solid ${active ? 'var(--color-primary)' : 'var(--color-border)'}`,
                        background: active ? 'color-mix(in srgb, var(--color-primary) 8%, transparent)' : 'var(--color-bg)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)' }}>
                          {item.display_name}
                        </span>
                        <StatusPill status={item.severity} />
                      </div>
                      <div style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)', marginTop: 3 }}>
                        {item.asset_id}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--color-fg-3)', marginTop: 2 }}>{localizedClassName(item.class_name, item.class_name_cn, lang)}</div>
                      <div style={{ fontSize: 11, color: 'var(--color-fg-4)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.signal}
                      </div>
                    </button>
                  );
                })}
              </div>
            </Section>
          </Card>

          {/* Main detail area */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {caseLoading && (
              <div style={{ padding: '12px 16px', background: 'var(--color-card)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 13, color: 'var(--color-fg-3)' }}>
                {t('assetCaseFile.loadingCaseFile')}
              </div>
            )}
            {caseError && (
              <div style={{ padding: '12px 16px', background: 'var(--color-danger-bg)', border: '1px solid var(--color-danger)', borderRadius: 10, fontSize: 13, color: 'var(--color-danger)' }}>
                {caseError}
              </div>
            )}
            {!caseLoading && !caseError && !caseFile && (
              <Card>
                <div style={{ padding: '40px 24px', textAlign: 'center' }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 40, color: 'var(--color-fg-4)', display: 'block', marginBottom: 12 }}>
                    {!indexLoading && caseIndex.length === 0 ? 'verified_user' : 'search'}
                  </span>
                  {!indexLoading && caseIndex.length === 0 ? (
                    <>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-fg-2)', marginBottom: 6 }}>
                        {t('assetCaseFile.portfolioHealthy')}
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--color-fg-4)', maxWidth: 320, margin: '0 auto', lineHeight: 1.6 }}>
                        {t('assetCaseFile.noAnomalies')}
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--color-fg-4)', marginTop: 12 }}>
                        <Trans
                          t={t}
                          i18nKey="assetCaseFile.useLookup"
                          components={{ strong: <strong style={{ color: 'var(--color-fg-3)' }} /> }}
                        />
                      </div>
                    </>
                  ) : (
                    <div style={{ fontSize: 13, color: 'var(--color-fg-4)' }}>
                      {t('assetCaseFile.selectCase')}
                    </div>
                  )}
                </div>
              </Card>
            )}

            {caseFile && (
              <>
                {/* Asset identity header */}
                <Card>
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
                    <div>
                      <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                        {caseFile.display_name || caseFile.asset_id}
                      </h2>
                      <div style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)', marginTop: 4 }}>
                        {caseFile.asset_id}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--color-fg-3)', marginTop: 6 }}>
                        {caseFile.breadcrumb.portfolio}
                        <span style={{ margin: '0 4px' }}>›</span>
                        {caseFile.breadcrumb.asset_class}
                        <span style={{ margin: '0 4px' }}>›</span>
                        {caseFile.display_name || caseFile.asset_id}
                      </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                        {t('assetCaseFile.caseId', { id: caseFile.asset_id })}
                      </span>
                      <StatusPill status={caseFile.severity}>
                        {t('assetCaseFile.severity', { severity: caseFile.severity })}
                      </StatusPill>
                    </div>
                  </div>
                </Card>

                {/* Three metric cards */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                  {/* Current State */}
                  <Card>
                    <Section icon="trending_up" title={t('assetCaseFile.currentActiveState')}>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 700, color: 'var(--color-fg-1)', margin: '8px 0 4px' }}>
                        {t('assetCaseFile.shares', { count: caseFile.current_state.current_quantity })}
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--color-fg-3)', marginBottom: 10 }}>
                        {t('assetCaseFile.marketValue')}
                        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-fg-1)' }}>
                          ¥{Math.round(caseFile.current_state.current_market_value).toLocaleString()}
                        </span>
                      </div>
                      <Pill tone="primary">{caseFile.current_state.active_source}</Pill>
                    </Section>
                  </Card>

                  {/* Authority / Legacy */}
                  <Card>
                    <Section icon="verified" title={t('assetCaseFile.authorityLegacy')}>
                      <dl style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
                        <div>
                          <dt style={{ color: 'var(--color-fg-4)', marginBottom: 2 }}>{t('assetCaseFile.expectedAuthority')}</dt>
                          <dd style={{ margin: 0, color: 'var(--color-fg-1)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                            {caseFile.authority_context.expected_authority_source}
                          </dd>
                        </div>
                        <div>
                          <dt style={{ color: 'var(--color-fg-4)', marginBottom: 2 }}>{t('assetCaseFile.legacyInfluence')}</dt>
                          <dd style={{ margin: 0, color: 'var(--color-fg-1)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                            {caseFile.authority_context.competing_sources.join(', ') || t('assetCaseFile.none')}
                          </dd>
                        </div>
                        <div>
                          <dt style={{ color: 'var(--color-fg-4)', marginBottom: 2 }}>{t('assetCaseFile.shadowConflict')}</dt>
                          <dd style={{ margin: 0 }}>
                            <StatusPill status={caseFile.authority_context.shadow_conflict_flag ? 'warning' : 'ok'}>
                              {caseFile.authority_context.shadow_conflict_flag ? t('assetCaseFile.detected') : t('assetCaseFile.none')}
                            </StatusPill>
                          </dd>
                        </div>
                      </dl>
                    </Section>
                  </Card>

                  {/* Quick Actions */}
                  <Card style={{ background: 'color-mix(in srgb, var(--color-primary) 5%, var(--color-card))' }}>
                    <Section icon="bolt" title={t('assetCaseFile.quickActions')}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        <ActionBtn
                          icon="receipt_long"
                          variant="secondary"
                          onClick={() => navigate(caseFile.quick_actions.transactions)}
                        >
                          {t('assetCaseFile.openTransactionEvidence')}
                        </ActionBtn>
                        <ActionBtn
                          icon="sync"
                          variant="secondary"
                          onClick={() => navigate(caseFile.quick_actions.sync_history)}
                        >
                          {t('assetCaseFile.openSyncHistory')}
                        </ActionBtn>
                      </div>
                      <dl style={{ margin: '14px 0 0', display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--color-fg-4)' }}>{t('assetCaseFile.snapshots')}</span>
                          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-fg-1)' }}>
                            {caseFile.evidence_counts.snapshots}
                          </span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--color-fg-4)' }}>{t('assetCaseFile.transactions')}</span>
                          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-fg-1)' }}>
                            {caseFile.evidence_counts.transactions}
                          </span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--color-fg-4)' }}>{t('assetCaseFile.syncRuns')}</span>
                          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-fg-1)' }}>
                            {caseFile.evidence_counts.sync_runs}
                          </span>
                        </div>
                      </dl>
                    </Section>
                  </Card>
                </div>

                {/* Issue Summary + Source Trace */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 16, alignItems: 'start' }}>
                  <Card>
                    <Section icon="report_problem" title={t('assetCaseFile.issueSummary')}>
                      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {caseFile.signals.map((signal, idx) => (
                          <li key={idx} style={{ display: 'flex', gap: 8, fontSize: 12, color: 'var(--color-fg-2)', alignItems: 'flex-start' }}>
                            <span
                              className="material-symbols-outlined"
                              style={{ fontSize: 15, flexShrink: 0, marginTop: 1, color: signalHealthy(signal) ? 'var(--color-success)' : 'var(--color-danger)' }}
                            >
                              {signalHealthy(signal) ? 'check_circle' : 'error'}
                            </span>
                            <span>{signal}</span>
                          </li>
                        ))}
                      </ul>
                    </Section>
                  </Card>

                  <Card>
                    <Section icon="account_tree" title={t('assetCaseFile.sourceTrace')}>
                      {caseFile.source_trace.length === 0 ? (
                        <div style={{ fontSize: 12, color: 'var(--color-fg-4)', padding: '12px 0' }}>
                          {t('assetCaseFile.noSourceTrace')}
                        </div>
                      ) : (
                        <OpsTable<TraceEvent>
                          cols={getTraceCols(t)}
                          rows={caseFile.source_trace}
                          density="dense"
                        />
                      )}
                    </Section>
                  </Card>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
