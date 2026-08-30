/**
 * CashFlowClassification — Operations page
 *
 * Data-quality classification of investment cash flows.
 * Tagging a flow as `external_contribution` is what feeds the North Star
 * glide-path run-rate computation.
 *
 * Features:
 *   • Filter tabs: Unclassified | Classified | All
 *   • Full paginated table (50 rows/page — no 20-row cap)
 *   • Per-row tag dropdown (single tag / change tag / untag)
 *   • Multi-select + bulk "Tag as" / "Untag" action bar
 *   • Auto-classify: dry-run → confirm → undo flow
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { api } from '../src/services/api';
import type {
    UnclassifiedFlow,
    ClassifiedFlow,
    CashFlowClassification as CashFlowClassificationEnum,
    FlowClassifySummary,
} from '../src/services/api/types';
import {
    ActionBtn,
    Card,
    ChipBtn,
    OpsSelect,
    Pill,
    Section,
    Toolbar,
} from '../components/operations';
import { useCurrency } from '../src/context/useCurrency';

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

type TabKey = 'unclassified' | 'classified' | 'all';

function classificationLabels(t: TFunction): Record<CashFlowClassificationEnum, string> {
    return {
        external_contribution: t('operations:cashFlowClassification.label.externalContribution'),
        internal_transfer: t('operations:cashFlowClassification.label.internalTransfer'),
        income_reinvested: t('operations:cashFlowClassification.label.incomeReinvested'),
    };
}

function classificationOptions(t: TFunction): { value: CashFlowClassificationEnum; label: string }[] {
    const labels = classificationLabels(t);
    return [
        { value: 'external_contribution', label: labels.external_contribution },
        { value: 'internal_transfer', label: labels.internal_transfer },
        { value: 'income_reinvested', label: labels.income_reinvested },
    ];
}

// ── Unified row shape ─────────────────────────────────────────────────────────

type FlowRow = {
    source_table: 'transactions' | 'income_expense_monthly' | 'fs_cash_delta';
    source_row_key: string;
    flow_date: string | null;
    transaction_type: string | null;
    asset_id: string | null;
    amount_cny: number | null;
    classification: CashFlowClassificationEnum | null;
    tagged_by: 'heuristic' | 'manual' | null;
    rule_id: string | null;
    note: string | null;
    /** V81: the tag's underlying transaction can no longer be resolved
     *  (re-imported with a different identity, or genuinely deleted). */
    orphaned: boolean;
};

function fromUnclassified(row: UnclassifiedFlow): FlowRow {
    return {
        source_table: row.source_table,
        source_row_key: row.source_row_key,
        flow_date: row.flow_date,
        transaction_type: row.transaction_type,
        asset_id: row.asset_id,
        amount_cny: row.amount_cny,
        classification: null,
        tagged_by: null,
        rule_id: null,
        note: null,
        orphaned: false,
    };
}

function fromClassified(row: ClassifiedFlow): FlowRow {
    return {
        source_table: row.source_table,
        source_row_key: row.source_row_key,
        flow_date: row.flow_date,
        transaction_type: row.transaction_type ?? null,
        asset_id: row.asset_id,
        amount_cny: row.amount_cny,
        classification: row.classification,
        tagged_by: row.tagged_by,
        rule_id: row.rule_id ?? null,
        note: row.note,
        orphaned: row.orphaned ?? false,
    };
}

function rowKey(row: FlowRow): string {
    return `${row.source_table}::${row.source_row_key}`;
}

// ── Pill helpers ─────────────────────────────────────────────────────────────

function ClassificationPill({ value }: { value: CashFlowClassificationEnum | null }) {
    const { t } = useTranslation('operations');
    if (!value) {
        return (
            <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                background: 'var(--color-bg)', border: '1px solid var(--color-border)',
                color: 'var(--color-fg-4)',
            }}>
                {t('cashFlowClassification.untagged')}
            </span>
        );
    }
    const colorMap: Record<CashFlowClassificationEnum,{ bg: string; color: string; border: string }> = {
        external_contribution: {
            bg: 'rgba(16,185,129,0.08)', color: 'var(--color-success)', border: 'rgba(16,185,129,0.25)',
        },
        internal_transfer: {
            bg: 'rgba(99,102,241,0.08)', color: '#6366f1', border: 'rgba(99,102,241,0.25)',
        },
        income_reinvested: {
            bg: 'rgba(245,158,11,0.08)', color: 'var(--color-warning)', border: 'rgba(245,158,11,0.25)',
        },
    };
    const c = colorMap[value];
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600,
            background: c.bg, border: `1px solid ${c.border}`, color: c.color,
        }}>
            {classificationLabels(t)[value]}
        </span>
    );
}

// ── Page component ────────────────────────────────────────────────────────────

export const CashFlowClassification: React.FC = () => {
    const { t } = useTranslation('operations');
    const { currency, convertFromCNY, currencySymbol } = useCurrency();

    /** Format a CNY value for display in the selected reporting currency. */
    const fmtAmt = (n: number | null | undefined, opts: { compact?: boolean; signed?: boolean } = {}): string => {
        if (n == null) return '—';
        const { compact = false, signed = false } = opts;
        const displayVal = convertFromCNY(n);
        const sign = signed ? (displayVal > 0 ? '+' : displayVal < 0 ? '−' : '') : displayVal < 0 ? '−' : '';
        const abs = Math.abs(displayVal);
        if (compact) {
            if (abs >= 1_000_000) return `${sign}${currencySymbol}${(abs / 1_000_000).toFixed(2)}M`;
            if (abs >= 1_000) return `${sign}${currencySymbol}${(abs / 1_000).toFixed(1)}K`;
            return `${sign}${currencySymbol}${abs.toFixed(0)}`;
        }
        const locale = currency === 'USD' ? 'en-US' : 'en-US';
        return `${sign}${currencySymbol}${abs.toLocaleString(locale, { maximumFractionDigits: 0 })}`;
    };

    const [tab, setTab] = useState<TabKey>('unclassified');
    const [unclassifiedRows, setUnclassifiedRows] = useState<FlowRow[]>([]);
    const [classifiedRows, setClassifiedRows] = useState<FlowRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Pagination
    const [page, setPage] = useState(0);

    // Selection
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [bulkClassification, setBulkClassification] = useState<CashFlowClassificationEnum | ''>('');

    // Per-row tag pending state
    const [tagPending, setTagPending] = useState<Set<string>>(new Set());
    const [tagError, setTagError] = useState<string | null>(null);

    // Bulk action pending
    const [bulkPending, setBulkPending] = useState(false);
    const [bulkError, setBulkError] = useState<string | null>(null);

    // Auto-classify states
    const [classifying, setClassifying] = useState(false);
    const [classifyPreview, setClassifyPreview] = useState<{ would_tag: number; unclassified_count: number } | null>(null);
    const [classifyResult, setClassifyResult] = useState<FlowClassifySummary | null>(null);
    const [classifyError, setClassifyError] = useState<string | null>(null);
    const [lastTaggedIds, setLastTaggedIds] = useState<number[]>([]);
    const [reverting, setReverting] = useState(false);

    // ── Data loading ─────────────────────────────────────────────────────────

    const loadAll = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [u, c] = await Promise.all([
                api.getUnclassifiedFlows(),
                api.getClassifiedFlows(),
            ]);
            setUnclassifiedRows(u.map(fromUnclassified));
            setClassifiedRows(c.map(fromClassified));
        } catch (e) {
            console.error('Failed to load cash flows:', e);
            setError(t('cashFlowClassification.loadError'));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => { loadAll(); }, [loadAll]);

    // Reset page + selection when tab changes
    useEffect(() => { setPage(0); setSelected(new Set()); }, [tab]);

    // ── Derived rows for current tab ─────────────────────────────────────────

    const displayRows = useMemo<FlowRow[]>(() => {
        if (tab === 'unclassified') return unclassifiedRows;
        if (tab === 'classified') return classifiedRows;
        // All: classified first (with tag), then unclassified
        const classifiedKeys = new Set(classifiedRows.map(rowKey));
        const unclassifiedOnly = unclassifiedRows.filter(r => !classifiedKeys.has(rowKey(r)));
        return [...classifiedRows, ...unclassifiedOnly];
    }, [tab, unclassifiedRows, classifiedRows]);

    const orphanedCount = useMemo(
        () => classifiedRows.filter(r => r.orphaned).length,
        [classifiedRows],
    );

    const hasFsCashDelta = useMemo(
        () => unclassifiedRows.some(r => r.source_table === 'fs_cash_delta')
            || classifiedRows.some(r => r.source_table === 'fs_cash_delta'),
        [unclassifiedRows, classifiedRows],
    );

    const totalPages = Math.max(1, Math.ceil(displayRows.length / PAGE_SIZE));
    const pageRows = displayRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

    // ── Selection helpers ────────────────────────────────────────────────────

    const pageKeys = useMemo(() => pageRows.map(rowKey), [pageRows]);
    const allPageSelected = pageKeys.length > 0 && pageKeys.every(k => selected.has(k));
    const someSelected = selected.size > 0;

    const toggleSelectAll = () => {
        if (allPageSelected) {
            setSelected(prev => { const n = new Set(prev); pageKeys.forEach(k => n.delete(k)); return n; });
        } else {
            setSelected(prev => { const n = new Set(prev); pageKeys.forEach(k => n.add(k)); return n; });
        }
    };

    const toggleRow = (key: string) => {
        setSelected(prev => {
            const n = new Set(prev);
            if (n.has(key)) n.delete(key); else n.add(key);
            return n;
        });
    };

    // ── Per-row tag ──────────────────────────────────────────────────────────

    const handleTagRow = async (row: FlowRow, classification: CashFlowClassificationEnum | '') => {
        const key = rowKey(row);
        if (classification === '') {
            // Untag
            setTagPending(p => new Set(p).add(key));
            setTagError(null);
            try {
                await api.untagFlows({ items: [{ source_table: row.source_table, source_row_key: row.source_row_key }] });
                await loadAll();
            } catch (e) {
                console.error('Untag failed:', e);
                setTagError(t('cashFlowClassification.untagError'));
            } finally {
                setTagPending(p => { const n = new Set(p); n.delete(key); return n; });
            }
        } else {
            setTagPending(p => new Set(p).add(key));
            setTagError(null);
            try {
                await api.tagFlow({ source_table: row.source_table, source_row_key: row.source_row_key, classification });
                await loadAll();
            } catch (e) {
                console.error('Tag flow failed:', e);
                setTagError(t('cashFlowClassification.tagError'));
            } finally {
                setTagPending(p => { const n = new Set(p); n.delete(key); return n; });
            }
        }
    };

    // ── Bulk actions ─────────────────────────────────────────────────────────

    const selectedRows = useMemo(
        () => displayRows.filter(r => selected.has(rowKey(r))),
        [displayRows, selected],
    );

    const handleBulkTag = async () => {
        if (!bulkClassification || selectedRows.length === 0) return;
        setBulkPending(true);
        setBulkError(null);
        try {
            await api.tagFlowsBulk({
                items: selectedRows.map(r => ({ source_table: r.source_table, source_row_key: r.source_row_key })),
                classification: bulkClassification,
            });
            setSelected(new Set());
            setBulkClassification('');
            await loadAll();
        } catch (e) {
            console.error('Bulk tag failed:', e);
            setBulkError(t('cashFlowClassification.bulkTagError'));
        } finally {
            setBulkPending(false);
        }
    };

    const handleBulkUntag = async () => {
        if (selectedRows.length === 0) return;
        setBulkPending(true);
        setBulkError(null);
        try {
            await api.untagFlows({
                items: selectedRows.map(r => ({ source_table: r.source_table, source_row_key: r.source_row_key })),
            });
            setSelected(new Set());
            await loadAll();
        } catch (e) {
            console.error('Bulk untag failed:', e);
            setBulkError(t('cashFlowClassification.bulkUntagError'));
        } finally {
            setBulkPending(false);
        }
    };

    // ── Auto-classify flow ───────────────────────────────────────────────────

    const handleAutoClassifyDryRun = async () => {
        setClassifying(true);
        setClassifyError(null);
        setClassifyResult(null);
        try {
            const preview = await api.classifyFlows(true);
            setClassifyPreview({
                would_tag: preview.would_tag ?? preview.tagged,
                unclassified_count: preview.unclassified_count,
            });
        } catch (e) {
            console.error('Auto-classify dry-run failed:', e);
            setClassifyError(t('cashFlowClassification.autoClassifyError'));
        } finally {
            setClassifying(false);
        }
    };

    const handleAutoClassifyConfirm = async () => {
        setClassifying(true);
        setClassifyError(null);
        try {
            const result = await api.classifyFlows(false);
            setLastTaggedIds(result.tagged_ids ?? []);
            setClassifyPreview(null);
            setClassifyResult(result);
            await loadAll();
        } catch (e) {
            console.error('Auto-classify failed:', e);
            setClassifyError(t('cashFlowClassification.autoClassifyError'));
        } finally {
            setClassifying(false);
        }
    };

    const handleUndoClassify = async () => {
        if (lastTaggedIds.length === 0) return;
        setReverting(true);
        setClassifyError(null);
        try {
            await api.revertFlowClassify(lastTaggedIds);
            setLastTaggedIds([]);
            setClassifyResult(null);
            await loadAll();
        } catch (e) {
            console.error('Revert classify failed:', e);
            setClassifyError(t('cashFlowClassification.undoError'));
        } finally {
            setReverting(false);
        }
    };

    // ── Render ───────────────────────────────────────────────────────────────

    return (
        <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

                {/* Header */}
                <Card style={{ padding: 0, overflow: 'hidden' }}>
                    <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
                        <span className="uis-eyebrow" style={{ fontSize: 10 }}>{t('cashFlowClassification.breadcrumb')}</span>
                    </div>
                    <div style={{
                        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
                        gap: 16, padding: '18px 20px', flexWrap: 'wrap',
                    }}>
                        <div>
                            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                                {t('cashFlowClassification.title')}
                            </h1>
                            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
                                <Trans
                                    t={t}
                                    i18nKey="cashFlowClassification.intro"
                                    components={{ strong: <strong /> }}
                                />
                            </p>
                            {/* Pending-classification status indicator — moved here from the
                                Forecast ▸ Cash Flow tab (R-4, plan §3 Section 3): it's a
                                data-quality signal, not a contribution figure, and a
                                navigate-away button is pointless on the page it points to.
                                `unclassifiedRows` is already loaded unconditionally by
                                `loadAll()` above, so this reuses live state rather than
                                adding a redundant fetch. */}
                            {unclassifiedRows.length > 0 && (
                                <p style={{
                                    display: 'flex', alignItems: 'center', gap: 6,
                                    margin: '8px 0 0', fontSize: 12, color: 'var(--zone-yellow-fg)',
                                }}>
                                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>pending_actions</span>
                                    {t('cashFlowClassification.pendingClassification', { count: unclassifiedRows.length })}
                                </p>
                            )}
                            {orphanedCount > 0 && (
                                <p style={{
                                    display: 'flex', alignItems: 'center', gap: 6,
                                    margin: '8px 0 0', fontSize: 12, color: 'var(--zone-yellow-fg)',
                                }}>
                                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>warning</span>
                                    {t('cashFlowClassification.orphanedTagsWarning', { count: orphanedCount })}
                                </p>
                            )}
                            {hasFsCashDelta && (
                                <p style={{
                                    display: 'flex', alignItems: 'center', gap: 6,
                                    margin: '8px 0 0', fontSize: 12, color: 'var(--color-fg-3)',
                                }}>
                                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>info</span>
                                    {t('cashFlowClassification.fsCashDeltaHint')}
                                </p>
                            )}
                        </div>
                        {/* Auto-classify controls */}
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                            {!classifyPreview && !classifyResult && (
                                <ActionBtn
                                    icon="auto_fix_high"
                                    variant="secondary"
                                    onClick={handleAutoClassifyDryRun}
                                    disabled={classifying}
                                >
                                    {classifying ? t('cashFlowClassification.runningEllipsis') : t('cashFlowClassification.autoClassify')}
                                </ActionBtn>
                            )}
                            {classifyPreview && (
                                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                                    <span style={{ fontSize: 12, color: 'var(--color-fg-2)' }}>
                                        <Trans
                                            t={t}
                                            i18nKey="cashFlowClassification.willTagPreview"
                                            values={{ wouldTag: classifyPreview.would_tag, unclassifiedCount: classifyPreview.unclassified_count }}
                                            components={{ strong1: <strong />, strong2: <strong /> }}
                                        />
                                    </span>
                                    <div style={{ display: 'flex', gap: 8 }}>
                                        <ActionBtn
                                            icon="check_circle"
                                            variant="primary"
                                            onClick={handleAutoClassifyConfirm}
                                            disabled={classifying}
                                        >
                                            {t('cashFlowClassification.confirm')}
                                        </ActionBtn>
                                        <ActionBtn
                                            icon="close"
                                            variant="secondary"
                                            onClick={() => setClassifyPreview(null)}
                                        >
                                            {t('cashFlowClassification.cancel')}
                                        </ActionBtn>
                                    </div>
                                </div>
                            )}
                            {classifyResult && (
                                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                                    <span style={{ fontSize: 12, color: 'var(--color-success)' }}>
                                        <Trans
                                            t={t}
                                            i18nKey="cashFlowClassification.taggedResult"
                                            values={{ tagged: classifyResult.tagged, remaining: classifyResult.unclassified_count }}
                                            components={{ strong: <strong /> }}
                                        />
                                    </span>
                                    <div style={{ display: 'flex', gap: 8 }}>
                                        {lastTaggedIds.length > 0 && (
                                            <ActionBtn
                                                icon="undo"
                                                variant="secondary"
                                                onClick={handleUndoClassify}
                                                disabled={reverting}
                                            >
                                                {reverting ? t('cashFlowClassification.undoingEllipsis') : t('cashFlowClassification.undo')}
                                            </ActionBtn>
                                        )}
                                        <ActionBtn
                                            icon="auto_fix_high"
                                            variant="secondary"
                                            onClick={() => { setClassifyResult(null); handleAutoClassifyDryRun(); }}
                                            disabled={classifying}
                                        >
                                            {t('cashFlowClassification.runAgain')}
                                        </ActionBtn>
                                    </div>
                                </div>
                            )}
                            {classifyError && (
                                <span style={{ fontSize: 12, color: 'var(--color-danger)' }}>{classifyError}</span>
                            )}
                        </div>
                    </div>
                </Card>

                {/* Global error */}
                {error && (
                    <div style={{
                        padding: '12px 16px', background: 'var(--color-danger-bg)',
                        border: '1px solid var(--color-danger)', borderRadius: 10,
                        fontSize: 13, color: 'var(--color-danger)',
                    }}>
                        {error}
                    </div>
                )}

                {/* Main table card */}
                <Card>
                    <Section icon="category" title={t('cashFlowClassification.flowRecords')}>
                        {/* Tab bar */}
                        <Toolbar style={{ marginBottom: 16 }}>
                            <ChipBtn primary={tab === 'unclassified'} onClick={() => setTab('unclassified')}>
                                {t('cashFlowClassification.tab.unclassified')}{!loading && <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.8 }}>({unclassifiedRows.length})</span>}
                            </ChipBtn>
                            <ChipBtn primary={tab === 'classified'} onClick={() => setTab('classified')}>
                                {t('cashFlowClassification.tab.classified')}{!loading && <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.8 }}>({classifiedRows.length})</span>}
                            </ChipBtn>
                            <ChipBtn primary={tab === 'all'} onClick={() => setTab('all')}>
                                {t('cashFlowClassification.tab.all')}
                            </ChipBtn>
                        </Toolbar>

                        {/* Bulk action bar */}
                        {someSelected && (
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: 10,
                                padding: '10px 14px', marginBottom: 12,
                                background: 'rgba(99,102,241,0.06)',
                                border: '1px solid rgba(99,102,241,0.2)',
                                borderRadius: 10,
                            }}>
                                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-fg-2)', marginRight: 4 }}>
                                    {t('cashFlowClassification.selectedCount', { count: selected.size })}
                                </span>
                                <OpsSelect
                                    value={bulkClassification}
                                    onChange={v => setBulkClassification(v as CashFlowClassificationEnum | '')}
                                    options={[
                                        { value: '', label: t('cashFlowClassification.tagAsEllipsis') },
                                        ...classificationOptions(t).map(o => ({ value: o.value, label: o.label })),
                                    ]}
                                    width={200}
                                />
                                <ActionBtn
                                    icon="sell"
                                    variant="primary"
                                    onClick={handleBulkTag}
                                    disabled={!bulkClassification || bulkPending}
                                >
                                    {t('cashFlowClassification.apply')}
                                </ActionBtn>
                                <ActionBtn
                                    icon="label_off"
                                    variant="secondary"
                                    onClick={handleBulkUntag}
                                    disabled={bulkPending}
                                >
                                    {t('cashFlowClassification.untagSelected')}
                                </ActionBtn>
                                <ActionBtn
                                    icon="close"
                                    variant="secondary"
                                    onClick={() => setSelected(new Set())}
                                >
                                    {t('cashFlowClassification.clear')}
                                </ActionBtn>
                                {bulkError && (
                                    <span style={{ fontSize: 12, color: 'var(--color-danger)', marginLeft: 4 }}>{bulkError}</span>
                                )}
                            </div>
                        )}

                        {/* Per-row tag error */}
                        {tagError && (
                            <div style={{
                                padding: '8px 12px', marginBottom: 10,
                                background: 'var(--color-danger-bg)',
                                border: '1px solid var(--color-danger)',
                                borderRadius: 8, fontSize: 12, color: 'var(--color-danger)',
                            }}>
                                {tagError}
                            </div>
                        )}

                        {loading ? (
                            <div style={{ padding: '32px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                {t('cashFlowClassification.loadingFlows')}
                            </div>
                        ) : displayRows.length === 0 ? (
                            <div style={{ padding: '32px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                {tab === 'unclassified'
                                    ? t('cashFlowClassification.emptyUnclassified')
                                    : tab === 'classified'
                                        ? t('cashFlowClassification.emptyClassified')
                                        : t('cashFlowClassification.emptyAll')}
                            </div>
                        ) : (
                            <>
                                {/* Table */}
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                        <thead>
                                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                {/* Checkbox col */}
                                                <th style={{ width: 36, padding: '8px 10px', textAlign: 'center' }}>
                                                    <input
                                                        type="checkbox"
                                                        checked={allPageSelected}
                                                        onChange={toggleSelectAll}
                                                        style={{ cursor: 'pointer' }}
                                                        aria-label={t('cashFlowClassification.selectAllOnPage')}
                                                    />
                                                </th>
                                                <Th style={{ width: 100 }}>{t('cashFlowClassification.col.date')}</Th>
                                                <Th style={{ width: 160 }}>{t('cashFlowClassification.col.type')}</Th>
                                                <Th>{t('cashFlowClassification.col.assetSource')}</Th>
                                                <Th align="right" style={{ width: 120 }}>{t('cashFlowClassification.col.amount', { currency })}</Th>
                                                <Th style={{ width: 180 }}>{t('cashFlowClassification.col.currentTag')}</Th>
                                                <Th style={{ width: 220 }}>{t('cashFlowClassification.col.tag')}</Th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {pageRows.map(row => {
                                                const key = rowKey(row);
                                                const isSelected = selected.has(key);
                                                const isPending = tagPending.has(key);
                                                return (
                                                    <tr
                                                        key={key}
                                                        style={{
                                                            borderBottom: '1px solid var(--color-border-soft)',
                                                            background: isSelected
                                                                ? 'rgba(99,102,241,0.04)'
                                                                : 'transparent',
                                                        }}
                                                    >
                                                        <td style={{ padding: '6px 10px', textAlign: 'center' }}>
                                                            <input
                                                                type="checkbox"
                                                                checked={isSelected}
                                                                onChange={() => toggleRow(key)}
                                                                style={{ cursor: 'pointer' }}
                                                            />
                                                        </td>
                                                        <td style={{ padding: '6px 10px', fontFamily: 'var(--font-mono)', color: 'var(--color-fg-2)', whiteSpace: 'nowrap' }}>
                                                            {row.flow_date ?? '—'}
                                                        </td>
                                                        <td style={{ padding: '6px 10px', color: 'var(--color-fg-2)' }}>
                                                            {row.transaction_type === 'income_expense_net'
                                                                ? <span style={{ color: 'var(--color-fg-3)', fontStyle: 'italic' }}>{t('cashFlowClassification.monthlyNet')}</span>
                                                                : row.transaction_type === 'cash_delta'
                                                                    ? <span style={{ color: 'var(--color-fg-3)', fontStyle: 'italic' }}>{t('cashFlowClassification.cashDeltaMonthly')}</span>
                                                                    : row.transaction_type
                                                                        ? row.transaction_type
                                                                        : <span style={{ color: 'var(--color-fg-4)' }}>—</span>
                                                            }
                                                        </td>
                                                        <td style={{ padding: '6px 10px', color: 'var(--color-fg-2)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                                                            {row.orphaned ? (
                                                                <Pill
                                                                    tone="warning"
                                                                    style={{ fontFamily: 'var(--font-sans)' }}
                                                                >
                                                                    <span
                                                                        title={t('cashFlowClassification.orphanedTitle')}
                                                                    >
                                                                        {t('cashFlowClassification.orphaned')}
                                                                    </span>
                                                                </Pill>
                                                            ) : row.source_table === 'fs_cash_delta' ? (
                                                                <span>
                                                                    {row.asset_id}
                                                                    <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-sans)' }}>
                                                                        {t('cashFlowClassification.financialSummary')}
                                                                    </span>
                                                                </span>
                                                            ) : row.asset_id ? (
                                                                <span>{row.asset_id}</span>
                                                            ) : row.source_table === 'income_expense_monthly' ? (
                                                                <span style={{ color: 'var(--color-fg-4)', fontStyle: 'italic', fontFamily: 'var(--font-sans)' }}>
                                                                    {t('cashFlowClassification.incomeExpenseMonthly')}
                                                                </span>
                                                            ) : (
                                                                <span style={{ color: 'var(--color-fg-4)' }}>
                                                                    {row.source_table}
                                                                </span>
                                                            )}
                                                        </td>
                                                        <td style={{ padding: '6px 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 600, color: row.amount_cny != null && row.amount_cny >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                                                            {fmtAmt(row.amount_cny)}
                                                        </td>
                                                        <td style={{ padding: '6px 10px' }}>
                                                            <ClassificationPill value={row.classification} />
                                                            {row.tagged_by === 'heuristic' && (
                                                                <span
                                                                    style={{ marginLeft: 4, fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}
                                                                    title={row.rule_id ? t('cashFlowClassification.autoTaggedByRule', { rule: row.rule_id }) : t('cashFlowClassification.autoTagged')}
                                                                >
                                                                    {t('cashFlowClassification.auto')}{row.rule_id ? ` · ${row.rule_id}` : ''}
                                                                </span>
                                                            )}
                                                        </td>
                                                        <td style={{ padding: '6px 10px' }}>
                                                            {isPending ? (
                                                                <span style={{ fontSize: 11, color: 'var(--color-fg-4)' }}>{t('cashFlowClassification.savingEllipsis')}</span>
                                                            ) : (
                                                                <RowTagSelect
                                                                    value={row.classification ?? ''}
                                                                    onChange={v => handleTagRow(row, v as CashFlowClassificationEnum | '')}
                                                                />
                                                            )}
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>

                                {/* Pagination */}
                                {totalPages > 1 && (
                                    <div style={{
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                        padding: '10px 14px', marginTop: 4,
                                        borderTop: '1px solid var(--color-border-soft)',
                                    }}>
                                        <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                                            {t('cashFlowClassification.flowsPage', { count: displayRows.length, page: page + 1, total: totalPages })}
                                        </span>
                                        <div style={{ display: 'flex', gap: 6 }}>
                                            <PagBtn onClick={() => setPage(0)} disabled={page === 0}>«</PagBtn>
                                            <PagBtn onClick={() => setPage(p => p - 1)} disabled={page === 0}>{t('cashFlowClassification.prev')}</PagBtn>
                                            <PagBtn onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1}>{t('cashFlowClassification.next')}</PagBtn>
                                            <PagBtn onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}>»</PagBtn>
                                        </div>
                                    </div>
                                )}

                                {/* Row count (no pagination) */}
                                {totalPages === 1 && (
                                    <div style={{ padding: '8px 14px', fontSize: 10, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                                        {t('cashFlowClassification.flowsCount', { count: displayRows.length })}
                                    </div>
                                )}
                            </>
                        )}
                    </Section>
                </Card>
            </div>
        </div>
    );
};

// ── Small helper components ───────────────────────────────────────────────────

const Th: React.FC<{
    children: React.ReactNode;
    align?: 'left' | 'right' | 'center';
    style?: React.CSSProperties;
}> = ({ children, align = 'left', style }) => (
    <th style={{
        padding: '8px 10px', textAlign: align,
        fontSize: 10, fontWeight: 700, letterSpacing: '0.04em',
        color: 'var(--color-fg-4)', textTransform: 'uppercase',
        ...style,
    }}>
        {children}
    </th>
);

const PagBtn: React.FC<{
    onClick: () => void;
    disabled?: boolean;
    children: React.ReactNode;
}> = ({ onClick, disabled, children }) => (
    <button
        onClick={onClick}
        disabled={disabled}
        style={{
            padding: '4px 10px', borderRadius: 7, border: '1px solid var(--color-border)',
            background: 'var(--color-card)', color: 'var(--color-fg-2)',
            fontSize: 12, fontFamily: 'var(--font-sans)', cursor: disabled ? 'default' : 'pointer',
            opacity: disabled ? 0.4 : 1,
        }}
    >
        {children}
    </button>
);

const RowTagSelect: React.FC<{
    value: CashFlowClassificationEnum | '';
    onChange: (v: CashFlowClassificationEnum | '') => void;
}> = ({ value, onChange }) => {
    const { t } = useTranslation('operations');
    return (
        <select
            value={value}
            onChange={e => onChange(e.target.value as CashFlowClassificationEnum | '')}
            style={{
                padding: '3px 8px', fontSize: 12, borderRadius: 7,
                border: '1px solid var(--color-border)',
                background: 'var(--color-card)',
                color: 'var(--color-fg-1)',
                fontFamily: 'var(--font-sans)',
                cursor: 'pointer',
                maxWidth: 200,
            }}
        >
            <option value="">{t('cashFlowClassification.untagOption')}</option>
            {classificationOptions(t).map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
            ))}
        </select>
    );
};
