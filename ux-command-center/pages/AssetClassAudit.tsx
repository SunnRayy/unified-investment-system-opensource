import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AssetClassInvestigation, OperationsAPI, PortfolioAuditSummary } from '../src/services/api';
import { useLanguage } from '../src/context/useLanguage';
import { localizedClassName } from '../src/utils/localizedClassName';
import {
  ActionBtn,
  Card,
  ColDef,
  OpsKpi,
  OpsSelect,
  OpsTable,
  Section,
  SourceChip,
  StatusPill,
  fmtCNY,
} from '../components/operations';

type FlattenedAsset = {
  asset_id: string;
  display_name: string;
  source_system: string;
  market_value: number;
  quantity: number | null;
  currency: string;
  legacy_influence: boolean;
  value_issue: boolean;
};

const assetStatus = (a: FlattenedAsset): 'warning' | 'review' | 'ok' =>
  a.value_issue ? 'warning' : a.legacy_influence ? 'review' : 'ok';

export const AssetClassAudit: React.FC = () => {
  const { t } = useTranslation(['operations', 'common']);
  const { lang } = useLanguage();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [portfolioAudit, setPortfolioAudit] = useState<PortfolioAuditSummary | null>(null);
  const [investigation, setInvestigation] = useState<AssetClassInvestigation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const className = searchParams.get('class') || '';
  const classOptions = useMemo(
    () => (portfolioAudit?.asset_classes || []).map((r) => ({
      value: r.class_name,
      label: localizedClassName(r.class_name, r.class_name_cn, lang),
    })),
    [portfolioAudit, lang],
  );

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const summary = await OperationsAPI.getPortfolioAudit();
        setPortfolioAudit(summary);
        const resolved = className || summary.asset_classes[0]?.class_name || '';
        if (resolved && resolved !== className) { setSearchParams({ class: resolved }); return; }
        if (!resolved) { setInvestigation(null); return; }
        const detail = await OperationsAPI.getAssetClassAudit(resolved);
        setInvestigation(detail);
      } catch (err) {
        console.error(err);
        setError(t('assetClassAudit.loadError'));
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [className, setSearchParams, t]);

  const assets = useMemo<FlattenedAsset[]>(() => {
    if (!investigation) return [];
    return investigation.groups.flatMap((g) =>
      g.assets.map((a) => ({
        asset_id: a.asset_id,
        display_name: a.display_name || a.asset_id,
        source_system: g.source_system,
        market_value: a.market_value || 0,
        quantity: (a as Record<string, unknown>).quantity != null ? Number((a as Record<string, unknown>).quantity) : null,
        currency: String((a as Record<string, unknown>).currency || 'CNY'),
        legacy_influence: !!a.legacy_influence,
        value_issue: !!a.value_issue,
      }))
    );
  }, [investigation]);

  const cols: ColDef<FlattenedAsset>[] = [
    { label: t('assetClassAudit.col.assetId'), key: 'asset_id', mono: true, width: 150 },
    { label: t('assetClassAudit.col.name'), key: 'display_name', width: 180 },
    { label: t('assetClassAudit.col.source'), render: (r) => <SourceChip name={r.source_system} />, width: 140 },
    {
      label: t('assetClassAudit.col.valueCny'), align: 'right', mono: true, width: 110,
      render: (r) => (
        <div>
          <div>{fmtCNY(r.market_value, { compact: true })}</div>
          {r.currency !== 'CNY' && (
            <div style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 1 }}>
              {t('assetClassAudit.sharesLine', { currency: r.currency, quantity: r.quantity != null ? r.quantity.toLocaleString() : '—' })}
            </div>
          )}
        </div>
      ),
    },
    { label: t('assetClassAudit.col.status'), align: 'center', width: 80, render: (r) => <StatusPill status={assetStatus(r)} /> },
    {
      label: t('assetClassAudit.col.action'), align: 'right', width: 120,
      render: (r) => (
        <ActionBtn
          icon="folder_open"
          onClick={() => navigate(`/asset-case-file?asset_id=${encodeURIComponent(r.asset_id)}`)}
        >
          {t('assetClassAudit.openCase')}
        </ActionBtn>
      ),
    },
  ];

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Header */}
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
            <span className="uis-eyebrow" style={{ fontSize: 10 }}>
              {t('assetClassAudit.breadcrumb')}
            </span>
          </div>
          <div style={{
            display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
            gap: 16, padding: '18px 20px', flexWrap: 'wrap',
          }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                {t('common:nav.assetClassAudit')}
              </h1>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
                {t('assetClassAudit.subtitle')}
              </p>
            </div>
            {classOptions.length > 0 && (
              <OpsSelect
                label={t('assetClassAudit.classLabel')}
                value={className}
                onChange={(v) => setSearchParams({ class: v })}
                options={classOptions}
                width={200}
              />
            )}
          </div>
        </Card>

        {loading && (
          <div style={{ padding: '12px 16px', background: 'var(--color-card)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 13, color: 'var(--color-fg-3)' }}>
            {t('assetClassAudit.loadingInvestigation')}
          </div>
        )}
        {error && (
          <div style={{ padding: '12px 16px', background: 'var(--color-danger-bg)', border: '1px solid var(--color-danger)', borderRadius: 10, fontSize: 13, color: 'var(--color-danger)' }}>
            {error}
          </div>
        )}

        {investigation && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
              <OpsKpi label={t('assetClassAudit.kpiAssetsInClass')} value={investigation.active_assets} icon="inventory_2" />
              <OpsKpi
                label={t('assetClassAudit.kpiTotalValue')}
                value={fmtCNY(investigation.total_value, { compact: true })}
                icon="account_balance_wallet"
              />
            </div>

            <Card>
              <Section
                icon="category"
                title={t('assetClassAudit.sectionTitle', { className: localizedClassName(investigation.class_name, investigation.class_name_cn, lang) })}
                right={
                  <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--color-fg-4)' }}>
                    {t('assetClassAudit.assetCount', { count: assets.length })}
                  </span>
                }
              >
                {assets.length === 0 ? (
                  <div style={{ padding: '20px 0', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                    {t('assetClassAudit.noAssetsInClass')}
                  </div>
                ) : (
                  <>
                    <div style={{ overflowX: 'auto' }}>
                    <OpsTable<FlattenedAsset>
                      cols={cols}
                      rows={assets}
                      rowKey={(r) => `${r.source_system}-${r.asset_id}`}
                      density="comfy"
                    />
                    </div>
                    <p style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 10, fontFamily: 'var(--font-mono)' }}>
                      {t('assetClassAudit.openCaseHint')}
                    </p>
                  </>
                )}
              </Section>
            </Card>
          </>
        )}
      </div>
    </div>
  );
};
