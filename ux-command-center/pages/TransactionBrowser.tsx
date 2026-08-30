import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { ManagementAPI, Transaction, TransactionFilters } from '../src/services/api';
import {
  ActionBtn,
  Card,
  ChipBtn,
  ColDef,
  IconBtn,
  OpsSelect,
  OpsTable,
  Pill,
  SearchInput,
  Section,
  Toolbar,
  fmtCNY,
} from '../components/operations';

type FilterState = {
  asset_id: string;
  source: string;
  normalized_type: string;
  raw_type: string;
  account: string;
  verified: string;
  date_from: string;
  date_to: string;
};

type DatePreset = '7d' | '30d' | '90d' | 'ytd' | 'all' | 'custom';

const PRESETS: Array<{ key: Exclude<DatePreset, 'custom'>; labelKey: string }> = [
  { key: '7d', labelKey: 'transactionBrowser.preset.7d' },
  { key: '30d', labelKey: 'transactionBrowser.preset.30d' },
  { key: '90d', labelKey: 'transactionBrowser.preset.90d' },
  { key: 'ytd', labelKey: 'transactionBrowser.preset.ytd' },
  { key: 'all', labelKey: 'transactionBrowser.preset.all' },
];

const initialFilters: FilterState = {
  asset_id: '', source: '', normalized_type: '', raw_type: '', account: '', verified: '',
  date_from: '', date_to: '',
};

const fmtDate = (d: Date) => d.toISOString().slice(0, 10);

const presetDates = (preset: Exclude<DatePreset, 'custom'>) => {
  const today = new Date();
  if (preset === 'all') return { date_from: '', date_to: '' };
  if (preset === 'ytd') return { date_from: `${today.getFullYear()}-01-01`, date_to: fmtDate(today) };
  const start = new Date();
  start.setDate(today.getDate() - (preset === '7d' ? 7 : preset === '30d' ? 30 : 90));
  return { date_from: fmtDate(start), date_to: fmtDate(today) };
};

const toCsv = (rows: Transaction[]) => {
  const headers = ['Date', 'Asset ID', 'Asset Name', 'Type', 'Source', 'Amount', 'Currency', 'Account', 'Memo'];
  const esc = (v: unknown) => {
    const s = v == null ? '' : String(v);
    return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = rows.map((r) => [
    r.transaction_date, r.asset_id, r.asset_name || '', r.transaction_type,
    r.source_system, r.amount_net ?? '', r.currency, r.account || '', r.memo || '',
  ].map(esc).join(','));
  return [headers.join(','), ...lines].join('\n');
};

const DetailRow: React.FC<{ label: string; value: React.ReactNode; color?: string }> = ({ label, value, color }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
    <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>{label}</span>
    <span style={{ fontSize: 12, fontWeight: 600, color: color ?? 'var(--color-fg-1)', fontFamily: 'var(--font-mono)', textAlign: 'right' }}>
      {value ?? '—'}
    </span>
  </div>
);

export const TransactionBrowser: React.FC = () => {
  const { t } = useTranslation('operations');
  const txCols: ColDef<Transaction>[] = useMemo(() => [
    { label: t('transactionBrowser.col.date'), key: 'transaction_date', mono: true, width: 100, nowrap: true },
    { label: t('transactionBrowser.col.assetId'), key: 'asset_id', mono: true, width: 130 },
    { label: t('transactionBrowser.col.name'), key: 'asset_name', width: 160 },
    { label: t('transactionBrowser.col.type'), key: 'transaction_type', width: 90 },
    { label: t('transactionBrowser.col.source'), render: (r) => <Pill tone="neutral">{r.source_system}</Pill>, width: 140 },
    {
      label: t('transactionBrowser.col.amount'), align: 'right', mono: true, width: 110,
      render: (r) => (
        <span style={{ color: (r.amount_net || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {r.amount_net != null ? (r.amount_net || 0).toLocaleString() : '—'}
        </span>
      ),
    },
    { label: t('transactionBrowser.col.ccy'), key: 'currency', mono: true, width: 50 },
  ], [t]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState<FilterState>(initialFilters);
  const [datePreset, setDatePreset] = useState<DatePreset>('30d');
  const [meta, setMeta] = useState<TransactionFilters>({ sources: [], raw_types: [], normalized_types: [], accounts: [] });
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);

  const queryFilters = useMemo<FilterState>(() => {
    const seed = { ...initialFilters };
    for (const key of Object.keys(seed) as Array<keyof FilterState>) {
      const v = searchParams.get(key);
      if (v) seed[key] = v;
    }
    return seed;
  }, [searchParams]);

  const updateQuery = (next: FilterState) => {
    const p = new URLSearchParams();
    (Object.entries(next) as Array<[keyof FilterState, string]>).forEach(([k, v]) => { if (v) p.set(k, v); });
    setSearchParams(p);
  };

  const search = async (f: FilterState) => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      (Object.entries(f) as Array<[keyof FilterState, string]>).forEach(([k, v]) => { if (v) params[k] = v; });
      const res = await ManagementAPI.searchTransactions(params);
      setTransactions(res.transactions);
      setSelected(res.transactions[0] || null);
    } catch (err) {
      console.error(err);
      setError(t('transactionBrowser.loadError'));
      setTransactions([]);
      setSelected(null);
    } finally {
      setLoading(false);
    }
  };

  const applyPreset = (preset: Exclude<DatePreset, 'custom'>) => {
    const dates = presetDates(preset);
    const next = { ...filters, ...dates };
    setDatePreset(preset);
    setFilters(next);
    updateQuery(next);
  };

  const handleExportCsv = () => {
    if (transactions.length === 0) return;
    const blob = new Blob([toCsv(transactions)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `transactions-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    ManagementAPI.getTransactionFilters().then(setMeta).catch(() => {});
  }, []);

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      if (!queryFilters.date_from && !queryFilters.date_to) {
        const defaults = { ...queryFilters, ...presetDates('30d') };
        setDatePreset('30d'); setFilters(defaults); updateQuery(defaults); return;
      }
    }
    setDatePreset(!queryFilters.date_from && !queryFilters.date_to ? 'all' : 'custom');
    setFilters(queryFilters);
    search(queryFilters);
  }, [queryFilters]);

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Header */}
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
            <span className="uis-eyebrow" style={{ fontSize: 10 }}>{t('transactionBrowser.breadcrumb')}</span>
          </div>
          <div style={{
            display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
            gap: 16, padding: '18px 20px', flexWrap: 'wrap',
          }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                {t('transactionBrowser.title')}
              </h1>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
                {t('transactionBrowser.subtitle')}
              </p>
            </div>
            <ActionBtn
              icon="download"
              variant="secondary"
              onClick={handleExportCsv}
              disabled={transactions.length === 0}
            >
              {t('transactionBrowser.exportCsv')}
            </ActionBtn>
          </div>
        </Card>

        {error && (
          <div style={{ padding: '12px 16px', background: 'var(--color-danger-bg)', border: '1px solid var(--color-danger)', borderRadius: 10, fontSize: 13, color: 'var(--color-danger)' }}>
            {error}
          </div>
        )}

        {/* Main two-column layout */}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 340px', gap: 16, alignItems: 'start' }}>

          {/* Filters + table */}
          <Card>
            <Section icon="filter_list" title={t('transactionBrowser.evidenceFilters')}>
              {/* Date presets */}
              <Toolbar style={{ marginBottom: 12 }}>
                {PRESETS.map((p) => (
                  <ChipBtn
                    key={p.key}
                    primary={datePreset === p.key}
                    onClick={() => applyPreset(p.key)}
                  >
                    {t(p.labelKey)}
                  </ChipBtn>
                ))}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' }}>
                  <input
                    type="date"
                    value={filters.date_from}
                    onChange={(e) => { setDatePreset('custom'); setFilters((f) => ({ ...f, date_from: e.target.value })); }}
                    style={{
                      padding: '5px 8px', fontSize: 12, borderRadius: 7,
                      border: '1px solid var(--color-border)', background: 'var(--color-card)',
                      color: 'var(--color-fg-1)', fontFamily: 'var(--font-sans)',
                    }}
                  />
                  <span style={{ fontSize: 11, color: 'var(--color-fg-4)' }}>{t('transactionBrowser.to')}</span>
                  <input
                    type="date"
                    value={filters.date_to}
                    onChange={(e) => { setDatePreset('custom'); setFilters((f) => ({ ...f, date_to: e.target.value })); }}
                    style={{
                      padding: '5px 8px', fontSize: 12, borderRadius: 7,
                      border: '1px solid var(--color-border)', background: 'var(--color-card)',
                      color: 'var(--color-fg-1)', fontFamily: 'var(--font-sans)',
                    }}
                  />
                </div>
              </Toolbar>

              {/* Dropdowns + search */}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
                <OpsSelect
                  value={filters.source}
                  onChange={(v) => setFilters((f) => ({ ...f, source: v }))}
                  options={[{ value: '', label: t('transactionBrowser.allSources') }, ...meta.sources.map((s) => ({ value: s, label: s }))]}
                  width={160}
                />
                <OpsSelect
                  value={filters.normalized_type}
                  onChange={(v) => setFilters((f) => ({ ...f, normalized_type: v }))}
                  options={[{ value: '', label: t('transactionBrowser.allTypes') }, ...meta.normalized_types.map((nt) => ({ value: nt, label: nt }))]}
                  width={140}
                />
                <OpsSelect
                  value={filters.verified}
                  onChange={(v) => setFilters((f) => ({ ...f, verified: v }))}
                  options={[
                    { value: '', label: t('transactionBrowser.verification') },
                    { value: 'true', label: t('transactionBrowser.verified') },
                    { value: 'false', label: t('transactionBrowser.unverified') },
                  ]}
                  width={130}
                />
                <SearchInput
                  value={filters.asset_id}
                  onChange={(v) => setFilters((f) => ({ ...f, asset_id: v }))}
                  placeholder={t('transactionBrowser.assetIdPlaceholder')}
                  width={160}
                />
                <ActionBtn variant="primary" icon="search" onClick={() => updateQuery(filters)}>
                  {t('transactionBrowser.search')}
                </ActionBtn>
              </div>
            </Section>

            {/* Transaction table */}
            {loading ? (
              <div style={{ padding: '24px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                {t('transactionBrowser.loadingTransactions')}
              </div>
            ) : transactions.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                {t('transactionBrowser.noTransactions')}
              </div>
            ) : (
              <>
                <div style={{ overflowX: 'auto' }}>
                  <OpsTable<Transaction>
                    cols={txCols}
                    rows={transactions}
                    rowKey={(r) => r.id}
                    selectedKey={selected?.id ?? null}
                    onRowClick={setSelected}
                    density="dense"
                  />
                </div>
                <div style={{ padding: '8px 14px', fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                  {t('transactionBrowser.transactionCount', { count: transactions.length })}
                </div>
              </>
            )}
          </Card>

          {/* Detail panel */}
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <Section icon="receipt_long" title={t('transactionBrowser.detailView')} style={{ flex: 1 }} />
              {selected && (
                <IconBtn icon="close" title={t('transactionBrowser.clearSelection')} onClick={() => setSelected(null)} />
              )}
            </div>

            {!selected ? (
              <div style={{ fontSize: 12, color: 'var(--color-fg-4)', padding: '12px 0' }}>
                {t('transactionBrowser.selectRowHint')}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div style={{
                  padding: '12px 14px',
                  background: 'var(--color-bg)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 8,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <span style={{ fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                      {t('transactionBrowser.transactionId')}
                    </span>
                    <Pill tone={selected.verified ? 'success' : 'neutral'}>
                      {selected.verified ? t('transactionBrowser.verified') : t('transactionBrowser.unverified')}
                    </Pill>
                  </div>
                  <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--color-fg-2)', marginBottom: 12 }}>
                    {t('transactionBrowser.trxPrefix')}{selected.id}-{selected.asset_id}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {/* These are raw API/DB field names shown verbatim as evidence-page labels,
                        same convention as an audit-log key — not translated, EN === zh (see
                        transactionBrowser.field.* in both catalogs). */}
                    <DetailRow label={t('transactionBrowser.field.transactionDate')} value={selected.transaction_date} />
                    <DetailRow
                      label={t('transactionBrowser.field.amountNet')}
                      value={selected.amount_net != null ? fmtCNY(selected.amount_net) : '—'}
                      color={(selected.amount_net || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)'}
                    />
                    <DetailRow label={t('transactionBrowser.field.priceUnit')} value={selected.price_unit ?? '—'} />
                    <DetailRow label={t('transactionBrowser.field.currency')} value={selected.currency} />
                    <DetailRow label={t('transactionBrowser.field.account')} value={selected.account || '—'} />
                    <DetailRow label={t('transactionBrowser.field.sourceSystem')} value={selected.source_system} />
                    <DetailRow label={t('transactionBrowser.field.memo')} value={selected.memo || '—'} />
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
