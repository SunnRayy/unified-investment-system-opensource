import React, { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { TaxonomyAPI, TaxonomyClass } from '../src/services/api';
import { useLanguage } from '../src/context/useLanguage';
import type { UiLocale } from '../src/utils/formatMoney';
import { formatCNY } from '../src/utils/format';
import { localizedClassName } from '../src/utils/localizedClassName';
import {
  ActionBtn,
  Card,
  ChipBtn,
  ColDef,
  FieldLabel,
  FormInput,
  FormSelect,
  Icon,
  ModalShell,
  OpsTable,
  Pill,
  SearchInput,
  Section,
  fmtCNY,
} from '../components/operations';

interface AuditAsset {
  asset_id: string;
  asset_name: string;
  asset_class: string | null;
  tier: string | null;
  source_system: string | null;
  class_name: string | null;
  class_name_cn: string | null;
  parent_class_name: string | null;
  parent_class_name_cn: string | null;
  market_value_cny: number | null;
  market_price: number | null;
  price_currency: string | null;
  price_source: string | null;
  quantity: number | null;
  snapshot_date: string | null;
}

interface ClassifyForm {
  rule_type: string;
  pattern: string;
  class_id: number | null;
  priority: number;
}

function fmtPrice(a: AuditAsset): string {
  if (a.market_price == null) return '—';
  const ccy = a.price_currency || 'CNY';
  return `${ccy} ${Number.parseFloat(Number(a.market_price).toFixed(4))}`;
}

const assetCols = (
  t: TFunction,
  onClassify: (a: AuditAsset) => void,
  onDeactivate: (id: string) => void,
  lang: UiLocale,
): ColDef<AuditAsset>[] => [
  { label: t('assetAudit.col.assetId'), key: 'asset_id', mono: true, width: 130 },
  {
    label: t('assetAudit.col.name'),
    width: 180,
    render: (a) => (
      <div>
        <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-fg-1)' }}>{a.asset_name || '—'}</div>
        {a.snapshot_date && (
          <div style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>
            {a.snapshot_date}
          </div>
        )}
      </div>
    ),
  },
  {
    label: t('assetAudit.col.class'),
    width: 120,
    render: (a) => a.class_name
      ? <Pill tone="primary">{localizedClassName(a.class_name, a.class_name_cn, lang)}</Pill>
      : <Pill tone="warning">{t('assetAudit.unclassifiedPill')}</Pill>,
  },
  {
    label: t('assetAudit.col.value'), align: 'right', mono: true, width: 110,
    render: (a) => a.market_value_cny != null ? fmtCNY(a.market_value_cny, { compact: true }) : '—',
  },
  { label: t('assetAudit.col.price'), align: 'right', mono: true, width: 110, render: fmtPrice },
  { label: t('assetAudit.col.holdingsSource'), key: 'source_system', mono: true, width: 130 },
  { label: t('assetAudit.col.priceSource'), key: 'price_source', mono: true, width: 110 },
  {
    label: t('assetAudit.col.actions'), align: 'right', width: 160,
    render: (a) => (
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <ChipBtn
          primary={!a.class_name}
          onClick={() => onClassify(a)}
        >
          {a.class_name ? t('assetAudit.reclassify') : t('assetAudit.classify')}
        </ChipBtn>
        <ActionBtn danger icon="block" onClick={() => onDeactivate(a.asset_id)}>
          {t('assetAudit.deactivate')}
        </ActionBtn>
      </div>
    ),
  },
];

export const AssetAudit: React.FC = () => {
  const { t } = useTranslation('operations');
  const { lang } = useLanguage();
  const [assets, setAssets] = useState<AuditAsset[]>([]);
  const [classes, setClasses] = useState<TaxonomyClass[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [tagging, setTagging] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const [classifyTarget, setClassifyTarget] = useState<AuditAsset | null>(null);
  const [classifyForm, setClassifyForm] = useState<ClassifyForm>({ rule_type: 'exact_id', pattern: '', class_id: null, priority: 50 });
  const [submittingClassify, setSubmittingClassify] = useState(false);

  const fetchData = async () => {
    setError(null);
    try {
      const [auditData, classesData] = await Promise.all([
        TaxonomyAPI.getAssetAudit(),
        TaxonomyAPI.getClasses(),
      ]);
      const list: AuditAsset[] = auditData.assets || [];
      setAssets(list);
      setClasses(classesData);
      setExpandedGroups(new Set(list.map((a) => a.parent_class_name || a.class_name || 'Unclassified')));
    } catch (err) {
      console.error('Failed to fetch asset audit:', err);
      setError(t('assetAudit.loadError'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  const handleAutoTag = async () => {
    setTagging(true);
    try {
      const result = await TaxonomyAPI.runAutoTag();
      setMessage({ text: t('assetAudit.autoTaggedMsg', { classified: result.classified, remaining: result.unclassified }), type: 'success' });
      fetchData();
    } catch {
      setMessage({ text: t('assetAudit.autoTaggingFailed'), type: 'error' });
    } finally {
      setTagging(false);
    }
  };

  const openClassify = (asset: AuditAsset) => {
    setClassifyTarget(asset);
    const flat = classes.flatMap((c) => [c, ...(c.children || [])]);
    const classId = asset.class_name ? flat.find((c) => c.name === asset.class_name)?.id ?? null : null;
    setClassifyForm({ rule_type: 'exact_id', pattern: asset.asset_id, class_id: classId, priority: 50 });
  };

  const submitClassify = async () => {
    if (!classifyForm.class_id) { alert(t('assetAudit.selectClassAlert')); return; }
    setSubmittingClassify(true);
    try {
      const result = await TaxonomyAPI.upsertRule({
        rule_type: classifyForm.rule_type,
        pattern: classifyForm.pattern,
        class_id: classifyForm.class_id,
        priority: classifyForm.priority,
        source: 'manual',
      });
      const verbKey = result.action === 'updated' ? 'assetAudit.ruleUpdated' : 'assetAudit.ruleCreated';
      setMessage({ text: t(verbKey, { assetId: classifyTarget?.asset_id }), type: 'success' });
      setClassifyTarget(null);
      fetchData();
    } catch (err) {
      console.error(err);
      setMessage({ text: t('assetAudit.saveRuleFailed'), type: 'error' });
    } finally {
      setSubmittingClassify(false);
    }
  };

  const handleClassifySubmit = (e: React.FormEvent) => { e.preventDefault(); submitClassify(); };

  const handleDeactivate = async (assetId: string) => {
    if (!window.confirm(t('assetAudit.deactivateConfirm', { assetId }))) return;
    try {
      await TaxonomyAPI.deactivateAsset(assetId);
      setMessage({ text: t('assetAudit.deactivatedMsg', { assetId }), type: 'success' });
      fetchData();
    } catch (err) {
      console.error(err);
      setMessage({ text: t('assetAudit.deactivateFailed'), type: 'error' });
    }
  };

  const toggleGroup = (group: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      next.has(group) ? next.delete(group) : next.add(group);
      return next;
    });
  };

  const unclassifiedCount = assets.filter((a) => !a.class_name).length;
  const totalValue = assets.reduce((s, a) => s + (a.market_value_cny || 0), 0);

  const filtered = assets.filter((a) =>
    !search
    || a.asset_id.toLowerCase().includes(search.toLowerCase())
    || (a.asset_name || '').toLowerCase().includes(search.toLowerCase())
  );

  const groupMap: Record<string, AuditAsset[]> = {};
  // Chinese companion per group key — same functional dependency as the key
  // itself (all assets sharing a parent_class_name/class_name share its cn).
  const groupNameCn: Record<string, string | null | undefined> = {};
  for (const a of filtered) {
    const g = a.parent_class_name || a.class_name || 'Unclassified';
    if (!groupMap[g]) {
      groupMap[g] = [];
      groupNameCn[g] = a.parent_class_name ? a.parent_class_name_cn : a.class_name_cn;
    }
    groupMap[g].push(a);
  }
  const orderedGroups = [
    ...Object.keys(groupMap).filter((g) => g !== 'Unclassified').sort(),
    ...(groupMap['Unclassified'] ? ['Unclassified'] : []),
  ];

  const allClassesFlat = classes.flatMap((c) => [c, ...(c.children || [])]);
  const cols = assetCols(t, openClassify, handleDeactivate, lang);

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '48px 28px', textAlign: 'center', color: 'var(--color-fg-4)', fontSize: 13 }}>
        {t('assetAudit.loadingAssetAudit')}
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-red-700 text-sm">{error}</p>
          </div>
        )}

        {/* Header */}
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
            <span className="uis-eyebrow" style={{ fontSize: 10 }}>{t('assetAudit.breadcrumb')}</span>
          </div>
          <div style={{
            display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
            gap: 16, padding: '18px 20px', flexWrap: 'wrap',
          }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                {t('assetAudit.title')}
              </h1>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
                {t('assetAudit.assetsTotalValue', { count: assets.length, value: formatCNY(totalValue) })}
                {unclassifiedCount > 0 && (
                  <span style={{ color: 'var(--color-warning)', marginLeft: 8 }}>
                    · {t('assetAudit.unclassifiedCount', { count: unclassifiedCount })}
                  </span>
                )}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <SearchInput value={search} onChange={setSearch} placeholder={t('assetAudit.searchPlaceholder')} width={240} />
              <ActionBtn
                variant="primary"
                icon={tagging ? 'hourglass_empty' : 'auto_fix_high'}
                onClick={handleAutoTag}
                disabled={tagging}
              >
                {tagging ? t('assetAudit.runningEllipsis') : t('assetAudit.runAutoTagger')}
              </ActionBtn>
            </div>
          </div>
        </Card>

        {/* Status message */}
        {message && (
          <div style={{
            padding: '10px 16px', borderRadius: 10, fontSize: 13,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            background: message.type === 'success' ? 'var(--color-success-bg)' : 'var(--color-danger-bg)',
            border: `1px solid ${message.type === 'success' ? 'var(--color-success)' : 'var(--color-danger)'}`,
            color: message.type === 'success' ? 'var(--color-success)' : 'var(--color-danger)',
          }}>
            {message.text}
            <button
              onClick={() => setMessage(null)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', fontSize: 16, lineHeight: 1 }}
            >
              ×
            </button>
          </div>
        )}

        {/* Grouped accordion */}
        {orderedGroups.map((group) => {
          const groupAssets = groupMap[group] || [];
          const groupValue = groupAssets.reduce((s, a) => s + (a.market_value_cny || 0), 0);
          const isExpanded = expandedGroups.has(group);
          const isUnclassified = group === 'Unclassified';
          const groupLabel = isUnclassified
            ? t('assetAudit.unclassifiedGroup')
            : localizedClassName(group, groupNameCn[group], lang);

          return (
            <Card key={group} style={{ padding: 0, overflow: 'hidden' }}>
              <button
                onClick={() => toggleGroup(group)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center',
                  justifyContent: 'space-between', padding: '12px 18px',
                  background: 'var(--color-border-soft)', border: 'none',
                  cursor: 'pointer', gap: 12, textAlign: 'left',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Icon name={isExpanded ? 'expand_more' : 'chevron_right'} size={16} color="var(--color-fg-4)" />
                  <span style={{
                    fontSize: 13, fontWeight: 700,
                    color: isUnclassified ? 'var(--color-warning)' : 'var(--color-fg-1)',
                  }}>
                    {groupLabel}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                    {t('assetAudit.groupAssetsCount', { count: groupAssets.length })}
                  </span>
                </div>
                <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--color-fg-3)' }}>
                  {groupValue > 0 ? fmtCNY(groupValue, { compact: true }) : '—'}
                </span>
              </button>

              {isExpanded && (
                <div style={{ overflowX: 'auto' }}>
                  <OpsTable<AuditAsset>
                    cols={cols}
                    rows={groupAssets}
                    rowKey={(a) => a.asset_id}
                    density="comfy"
                  />
                </div>
              )}
            </Card>
          );
        })}

        {orderedGroups.length === 0 && (
          <div style={{ padding: '32px', textAlign: 'center', fontSize: 13, color: 'var(--color-fg-4)' }}>
            {t('assetAudit.noAssetsMatchSearch')}
          </div>
        )}
      </div>

      {/* Classify / Reclassify Modal */}
      {classifyTarget && (
        <ModalShell
          title={classifyTarget.class_name ? t('assetAudit.reclassifyAsset') : t('assetAudit.classifyAsset')}
          subtitle={classifyTarget.asset_id}
          onClose={() => setClassifyTarget(null)}
        >
          <form onSubmit={handleClassifySubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <FieldLabel>{t('assetAudit.ruleType')}</FieldLabel>
              <FormSelect
                value={classifyForm.rule_type}
                onChange={(v) => setClassifyForm((f) => ({ ...f, rule_type: v }))}
                options={[
                  { value: 'exact_id', label: t('assetAudit.ruleTypeOption.exactId') },
                  { value: 'exact_name', label: t('assetAudit.ruleTypeOption.exactName') },
                  { value: 'regex', label: t('assetAudit.ruleTypeOption.regex') },
                ]}
              />
            </div>
            <div>
              <FieldLabel>{t('assetAudit.pattern')}</FieldLabel>
              <FormInput
                value={classifyForm.pattern}
                onChange={(v) => setClassifyForm((f) => ({ ...f, pattern: v }))}
                placeholder={t('assetAudit.patternPlaceholder')}
                mono
              />
              <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-fg-4)' }}>
                <Trans
                  t={t}
                  i18nKey="assetAudit.patternHint"
                  components={{ code: <code /> }}
                />
              </div>
            </div>
            <div>
              <FieldLabel>{t('assetAudit.targetClass')}</FieldLabel>
              <FormSelect
                value={classifyForm.class_id != null ? String(classifyForm.class_id) : ''}
                onChange={(v) => setClassifyForm((f) => ({ ...f, class_id: v ? Number(v) : null }))}
                options={[
                  { value: '', label: t('assetAudit.selectClassEllipsis') },
                  ...allClassesFlat.map((c) => ({
                    value: String(c.id),
                    label: c.name + (c.name_cn ? ` (${c.name_cn})` : ''),
                  })),
                ]}
              />
            </div>
            <div>
              <FieldLabel>{t('assetAudit.priority')} <span style={{ fontWeight: 400, color: 'var(--color-fg-4)' }}>{t('assetAudit.priorityHint')}</span></FieldLabel>
              <FormInput
                type="number"
                value={String(classifyForm.priority)}
                onChange={(v) => setClassifyForm((f) => ({ ...f, priority: Number(v) }))}
              />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, paddingTop: 4 }}>
              <ActionBtn variant="secondary" onClick={() => setClassifyTarget(null)}>{t('assetAudit.cancel')}</ActionBtn>
              <ActionBtn variant="primary" disabled={submittingClassify}>
                {submittingClassify ? t('assetAudit.classifyingEllipsis') : t('assetAudit.createRuleApply')}
              </ActionBtn>
            </div>
          </form>
        </ModalShell>
      )}
    </div>
  );
};
