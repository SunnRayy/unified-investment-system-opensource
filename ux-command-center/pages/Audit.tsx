import React, { useEffect, useRef, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  AuditAPI,
  OnDemandAuditResult,
  OperationsAPI,
  PortfolioAuditSummary,
  SourceReconciliationRow,
  SyncChangelogEvent,
} from '../src/services/api';
import {
  ActionBtn,
  Card,
  ColDef,
  OpsKpi,
  OpsTable,
  Section,
  SourceChip,
  StatusPill,
  fmtCNY,
} from '../components/operations';
import { useLanguage } from '../src/context/useLanguage';
import { localizedClassName } from '../src/utils/localizedClassName';

type AssetClassRow = PortfolioAuditSummary['asset_classes'][number];

const CHANGELOG_COLOR: Record<SyncChangelogEvent['kind'], string> = {
  warning: 'var(--color-danger)',
  case: 'var(--color-warning)',
  info: 'var(--color-primary)',
};
const CHANGELOG_ICON: Record<SyncChangelogEvent['kind'], string> = {
  warning: 'warning',
  case: 'folder_open',
  info: 'info',
};

export const Audit: React.FC = () => {
  const { t } = useTranslation('operations');
  const { lang } = useLanguage();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<PortfolioAuditSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [auditRunning, setAuditRunning] = useState(false);
  const [auditMsg, setAuditMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [expandedCats, setExpandedCats] = useState<Record<string, boolean>>({});
  const [changelogExpanded, setChangelogExpanded] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const portfolio = await OperationsAPI.getPortfolioAudit();
      setSummary(portfolio);
      const initExpanded: Record<string, boolean> = {};
      portfolio.integrity_grouped.forEach((g) => {
        initExpanded[g.cat] = g.pass < g.total;
      });
      setExpandedCats(initExpanded);
    } catch (err) {
      console.error(err);
      setError(t('audit.loadError'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleRunAudit = async () => {
    setAuditRunning(true);
    setAuditMsg(null);
    try {
      const result: OnDemandAuditResult = await AuditAPI.runOnDemandAudit();
      setAuditMsg({ kind: 'ok', text: t('audit.auditComplete', { timestamp: new Date().toLocaleString() }) });
      await load();
    } catch (err) {
      console.error(err);
      setAuditMsg({ kind: 'err', text: t('audit.onDemandAuditFailed') });
    } finally {
      setAuditRunning(false);
    }
  };

  const integrityRef = useRef<HTMLDivElement>(null);
  const reconRef = useRef<HTMLDivElement>(null);

  const healthScore = summary
    ? Math.round((summary.integrity.passed / Math.max(summary.integrity.total, 1)) * 100)
    : 0;

  const changelogNavTarget = (title: string): string | null => {
    const t = title.toLowerCase();
    if (t.includes('cost basis') || t.includes('transaction')) return '/transactions';
    if (t.includes('allocation') || t.includes('drift')) return '/compass';
    if (t.includes('source count')) return null;
    return null;
  };

  const integrityInvestigateHint = (name: string): string => {
    if (name.includes('trade_log')) return t('audit.hint.tradeLog');
    if (name.includes('source_reconciliation')) return t('audit.hint.sourceReconciliation');
    if (name.includes('net_worth')) return t('audit.hint.netWorth');
    if (name.includes('cost_basis')) return t('audit.hint.costBasis');
    if (name.includes('shadow')) return t('audit.hint.shadow');
    return t('audit.hint.default');
  };

  const reconCols: ColDef<SourceReconciliationRow>[] = [
    { label: t('audit.recon.col.source'), key: 'source', mono: true },
    {
      label: t('audit.recon.col.dbCount'),
      align: 'right',
      mono: true,
      render: (r) => (
        <span>
          {r.db_count}
          {r.prior_count != null && (
            <span style={{
              marginLeft: 6, fontSize: 10,
              color: r.count_delta === 0 ? 'var(--color-fg-4)'
                : (r.count_delta ?? 0) > 0 ? 'var(--color-success)' : 'var(--color-danger)',
            }}>
              {r.count_delta === 0 ? '=' : `${(r.count_delta ?? 0) > 0 ? '+' : ''}${r.count_delta}`}
            </span>
          )}
        </span>
      ),
    },
    {
      label: t('audit.recon.col.dbValue'),
      align: 'right',
      mono: true,
      render: (r) => (
        <span>
          {fmtCNY(r.db_value, { compact: true })}
          {r.value_delta_pct != null && (
            <span style={{
              marginLeft: 6, fontSize: 10,
              color: r.value_delta_pct === 0 ? 'var(--color-fg-4)'
                : r.value_delta_pct > 0 ? 'var(--color-success)' : 'var(--color-danger)',
            }}>
              {r.value_delta_pct > 0 ? '+' : ''}{r.value_delta_pct}%
            </span>
          )}
        </span>
      ),
    },
    {
      label: t('audit.recon.col.status'),
      align: 'center',
      render: (r) => <StatusPill status={r.status} />,
    },
    {
      label: t('audit.recon.col.lastSync'),
      align: 'right',
      mono: true,
      render: (r) => r.last_sync ? r.last_sync.slice(0, 16).replace('T', ' ') : '—',
    },
  ];

  const classCols: ColDef<AssetClassRow>[] = [
    {
      label: t('audit.class.col.assetClass'),
      key: 'class_name',
      render: (r) => localizedClassName(r.class_name, r.class_name_cn, lang),
    },
    {
      label: t('audit.class.col.exposure'),
      align: 'right',
      mono: true,
      render: (r) => fmtCNY(r.current_value, { compact: true }),
    },
    {
      label: t('audit.class.col.status'),
      render: (r) => <StatusPill status={r.status} />,
    },
    {
      label: t('audit.class.col.sources'),
      render: (r) => (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {r.source_signal_summary.length > 0
            ? r.source_signal_summary.map((s) => (
                <SourceChip key={s.source_system} name={s.source_system} count={s.asset_count} />
              ))
            : <span style={{ color: 'var(--color-fg-4)', fontSize: 11 }}>—</span>}
        </div>
      ),
    },
    {
      label: t('audit.class.col.openCases'),
      align: 'right',
      mono: true,
      render: (r) => (
        <span style={{ color: r.open_case_count > 0 ? 'var(--color-warning)' : 'var(--color-fg-3)' }}>
          {r.open_case_count}
        </span>
      ),
    },
  ];

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Page header */}
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
            <span className="uis-eyebrow" style={{ fontSize: 10 }}>
              {t('audit.breadcrumb')}
            </span>
          </div>
          <div style={{
            display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
            gap: 16, padding: '18px 20px', flexWrap: 'wrap',
          }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                {t('audit.title')}
              </h1>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
                {t('audit.subtitle')}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <ActionBtn variant="secondary" icon="refresh" onClick={load}>{t('audit.refresh')}</ActionBtn>
              <ActionBtn
                variant="primary"
                icon={auditRunning ? 'progress_activity' : 'play_arrow'}
                onClick={handleRunAudit}
                disabled={auditRunning}
              >
                {auditRunning ? t('audit.runningEllipsis') : t('audit.runAudit')}
              </ActionBtn>
            </div>
          </div>
        </Card>

        {/* Status banners */}
        {loading && (
          <div style={{ padding: '12px 16px', background: 'var(--color-card)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 13, color: 'var(--color-fg-3)' }}>
            {t('audit.loadingPortfolioAudit')}
          </div>
        )}
        {error && (
          <div style={{ padding: '12px 16px', background: 'var(--color-danger-bg)', border: '1px solid var(--color-danger)', borderRadius: 10, fontSize: 13, color: 'var(--color-danger)' }}>
            {error}
          </div>
        )}
        {auditMsg && (
          <div style={{
            padding: '12px 16px', borderRadius: 10, fontSize: 13,
            background: auditMsg.kind === 'ok' ? 'var(--color-success-bg)' : 'var(--color-danger-bg)',
            border: `1px solid ${auditMsg.kind === 'ok' ? 'var(--color-success)' : 'var(--color-danger)'}`,
            color: auditMsg.kind === 'ok' ? 'var(--color-success)' : 'var(--color-danger)',
          }}>
            {auditMsg.text}
          </div>
        )}

        {summary && (
          <>
            {/* KPI strip */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
              <OpsKpi
                label={t('audit.kpi.healthScore')}
                value={`${healthScore}%`}
                accent={healthScore >= 90 ? 'var(--color-success)' : healthScore >= 70 ? 'var(--color-warning)' : 'var(--color-danger)'}
                icon="health_metrics"
                sub={t('audit.kpi.checksSub', { passed: summary.integrity.passed, total: summary.integrity.total })}
              />
              <div
                onClick={() => integrityRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                style={{ cursor: summary.open_anomalies > 0 ? 'pointer' : 'default' }}
                title={summary.open_anomalies > 0 ? t('audit.kpi.clickToViewFailing') : undefined}
              >
                <OpsKpi
                  label={t('audit.kpi.openCases')}
                  value={summary.open_anomalies}
                  icon="folder_open"
                  accent={summary.open_anomalies > 0 ? 'var(--color-warning)' : undefined}
                  sub={summary.open_anomalies === 0 ? t('audit.kpi.noActiveCases') : t('audit.kpi.seeIntegrityGate')}
                />
              </div>
              <OpsKpi
                label={t('audit.kpi.readerWarnings')}
                value={summary.reader_warnings}
                icon="warning"
                accent={summary.reader_warnings > 0 ? 'var(--color-danger)' : undefined}
                sub={summary.last_sync_timestamp ? t('audit.kpi.lastSyncSub', { date: summary.last_sync_timestamp.slice(0, 10) }) : t('audit.kpi.neverSynced')}
              />
              <OpsKpi
                label={t('audit.kpi.legacyInfluence')}
                value={summary.legacy_influence_cases}
                icon="history"
                sub={summary.legacy_influence_cases === 0 ? t('audit.kpi.noLegacyInfluence') : t('audit.kpi.shadowCases')}
              />
            </div>

            {/* Two-column: Sync Changelog + Integrity Gate */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 16, alignItems: 'start' }}>

              {/* Sync Changelog */}
              <Card>
                <Section
                  icon="history_edu"
                  title={t('audit.syncChangelog.title')}
                  eyebrow={t('audit.syncChangelog.eyebrow')}
                  right={
                    <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--color-fg-4)' }}>
                      {t('audit.syncChangelog.eventCount', { count: summary.sync_changelog.length })}
                    </span>
                  }
                >
                  {(() => {
                    const issues = summary.sync_changelog.filter((e) => e.kind !== 'info');
                    const hiddenCount = summary.sync_changelog.length - issues.length;
                    const visible = changelogExpanded ? summary.sync_changelog : issues;

                    if (summary.sync_changelog.length === 0) {
                      return (
                        <div style={{
                          padding: '20px 0', textAlign: 'center', fontSize: 12,
                          color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)',
                        }}>
                          {t('audit.syncChangelog.noChanges')}
                        </div>
                      );
                    }

                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {visible.length === 0 ? (
                          <div style={{
                            padding: '12px 0', textAlign: 'center', fontSize: 12,
                            color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)',
                          }}>
                            {t('audit.syncChangelog.noIssuesHidden', { count: hiddenCount })}
                          </div>
                        ) : (
                          visible.map((evt, i) => {
                            const navTarget = changelogNavTarget(evt.title);
                            return (
                              <div key={i} style={{
                                display: 'flex', gap: 10, alignItems: 'flex-start',
                                padding: '10px 12px',
                                background: 'var(--color-bg)',
                                border: '1px solid var(--color-border-soft)',
                                borderLeft: `3px solid ${CHANGELOG_COLOR[evt.kind]}`,
                                borderRadius: 8,
                              }}>
                                <span className="material-symbols-outlined" style={{
                                  fontSize: 14, color: CHANGELOG_COLOR[evt.kind], flexShrink: 0, marginTop: 1,
                                }}>
                                  {CHANGELOG_ICON[evt.kind]}
                                </span>
                                <div style={{ minWidth: 0, flex: 1 }}>
                                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)' }}>{evt.title}</div>
                                  <div style={{ fontSize: 11, color: 'var(--color-fg-3)', marginTop: 2 }}>{evt.detail}</div>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                                    <span style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                                      {evt.date}
                                    </span>
                                    {navTarget && (evt.kind === 'warning' || evt.kind === 'case') && (
                                      <button
                                        onClick={() => navigate(navTarget)}
                                        style={{
                                          background: 'none', border: 'none', padding: 0,
                                          cursor: 'pointer', fontSize: 10, color: 'var(--color-primary)',
                                          fontFamily: 'var(--font-mono)',
                                        }}
                                      >
                                        {t('audit.syncChangelog.investigate')}
                                      </button>
                                    )}
                                  </div>
                                </div>
                              </div>
                            );
                          })
                        )}
                        {hiddenCount > 0 && (
                          <button
                            onClick={() => setChangelogExpanded((v) => !v)}
                            style={{
                              background: 'none', border: '1px solid var(--color-border)',
                              borderRadius: 6, padding: '6px 12px', cursor: 'pointer',
                              fontSize: 11, color: 'var(--color-fg-3)', fontFamily: 'var(--font-mono)',
                              display: 'flex', alignItems: 'center', gap: 4, alignSelf: 'flex-start',
                            }}
                          >
                            <span className="material-symbols-outlined" style={{ fontSize: 12 }}>
                              {changelogExpanded ? 'expand_less' : 'expand_more'}
                            </span>
                            {changelogExpanded
                              ? t('audit.syncChangelog.showIssuesOnly')
                              : t('audit.syncChangelog.showAllEvents', { count: summary.sync_changelog.length, hidden: hiddenCount })}
                          </button>
                        )}
                      </div>
                    );
                  })()}
                </Section>
              </Card>

              {/* Integrity Gate */}
              <div ref={integrityRef} style={{ scrollMarginTop: 24 }}>
              <Card>
                <Section
                  icon="verified_user"
                  title={t('audit.integrityGate.title')}
                  right={
                    <span style={{
                      fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 700, padding: '3px 8px',
                      borderRadius: 6, border: '1px solid var(--color-border)',
                      background: summary.integrity.all_passed ? 'var(--color-success-bg)' : 'var(--color-warning-bg)',
                      color: summary.integrity.all_passed ? 'var(--color-success)' : 'var(--color-warning)',
                    }}>
                      {t('audit.integrityGate.passCount', { passed: summary.integrity.passed, total: summary.integrity.total })}
                    </span>
                  }
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {summary.integrity_grouped.map((group) => {
                      const allPassed = group.pass === group.total;
                      const expanded = !!expandedCats[group.cat];
                      return (
                        <div key={group.cat} style={{
                          border: '1px solid var(--color-border)',
                          borderRadius: 8, overflow: 'hidden',
                        }}>
                          <button
                            onClick={() => setExpandedCats((p) => ({ ...p, [group.cat]: !p[group.cat] }))}
                            style={{
                              width: '100%', display: 'flex', alignItems: 'center',
                              justifyContent: 'space-between', padding: '9px 12px',
                              background: 'var(--color-border-soft)', border: 'none',
                              cursor: 'pointer', gap: 8, textAlign: 'left',
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span className="material-symbols-outlined" style={{ fontSize: 14, color: 'var(--color-fg-4)' }}>
                                {expanded ? 'expand_more' : 'chevron_right'}
                              </span>
                              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-1)' }}>{group.cat}</span>
                            </div>
                            <span style={{
                              fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 700,
                              padding: '2px 7px', borderRadius: 5,
                              background: allPassed ? 'var(--color-success-bg)' : 'var(--color-warning-bg)',
                              color: allPassed ? 'var(--color-success)' : 'var(--color-warning)',
                            }}>
                              {group.pass}/{group.total}
                            </span>
                          </button>
                          {expanded && group.fails.length > 0 && (
                            <div style={{ borderTop: '1px solid var(--color-border-soft)' }}>
                              {group.fails.map((fail, fi) => (
                                <div key={fi} style={{
                                  padding: '8px 12px',
                                  borderBottom: fi < group.fails.length - 1 ? '1px solid var(--color-border-soft)' : 'none',
                                }}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-fg-1)' }}>{fail.name}</span>
                                    <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--color-danger)', fontFamily: 'var(--font-mono)' }}>{t('audit.integrityGate.fail')}</span>
                                  </div>
                                  <div style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)', marginTop: 3 }}>
                                    {t('audit.integrityGate.actualThreshold', { actual: fail.actual, threshold: fail.thr })}
                                  </div>
                                  {fail.details && (
                                    <div style={{ fontSize: 10, color: 'var(--color-danger)', marginTop: 2 }}>{fail.details}</div>
                                  )}
                                  <div style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 4, fontStyle: 'italic' }}>
                                    {integrityInvestigateHint(fail.name)}
                                  </div>
                                </div>
                              ))}
                              {group.pass > 0 && (
                                <div style={{ padding: '6px 12px', fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                                  {t('audit.integrityGate.passingNotShown', { count: group.pass })}
                                </div>
                              )}
                            </div>
                          )}
                          {expanded && group.fails.length === 0 && (
                            <div style={{ padding: '8px 12px', fontSize: 11, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>check_circle</span>
                              {t('audit.integrityGate.allPassing', { count: group.total })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </Section>
              </Card>
              </div>
            </div>

            {/* Source Reconciliation */}
            <Card>
              <Section icon="compare_arrows" title={t('audit.sourceReconciliation.title')} eyebrow={t('audit.sourceReconciliation.eyebrow')}>
                {summary.source_reconciliation.length === 0 ? (
                  <div style={{ padding: '20px 0', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                    {t('audit.sourceReconciliation.noData')}
                  </div>
                ) : (
                  <OpsTable<SourceReconciliationRow>
                    cols={reconCols}
                    rows={summary.source_reconciliation}
                    rowKey={(r) => r.source}
                    density="dense"
                  />
                )}
              </Section>
            </Card>

            {/* Asset Class Sync Activity */}
            <Card>
              <Section
                icon="category"
                title={t('audit.assetClassActivity.title')}
                right={
                  <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                    {t('audit.assetClassActivity.classesCount', { count: summary.asset_classes.length })}
                  </span>
                }
              >
                <OpsTable<AssetClassRow>
                  cols={classCols}
                  rows={summary.asset_classes}
                  rowKey={(r) => r.class_name}
                  density="comfy"
                  onRowClick={(r) => navigate(`/asset-class-audit?class=${encodeURIComponent(r.class_name)}`)}
                />
                <p style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 10, fontFamily: 'var(--font-mono)' }}>
                  {t('audit.assetClassActivity.clickRowHint')}
                </p>
              </Section>
            </Card>
          </>
        )}
      </div>
    </div>
  );
};

export default Audit;
