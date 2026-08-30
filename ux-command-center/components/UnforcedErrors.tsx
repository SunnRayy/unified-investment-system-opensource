/**
 * UnforcedErrors — self-contained section for non-compliant actions & execution mistakes.
 *
 * Ported from NorthStarPanel.tsx (WS-C 2026-07-12).
 * Preserves R2-7 behaviors:
 *   - Inline cost edit with (edited) history tooltip
 *   - + Log a new entry form (date / description / est-cost)
 *   - Fetches its own data via api.getUnforcedErrors()
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../src/services/api';
import type { UnforcedError, UnforcedErrorCreate } from '../src/services/api/types';

// ── Style helpers — match StrategyAlignment page conventions ─────────────────
const CARD = 'bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden';
const INPUT = 'w-full text-sm px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors';
const BTN_PRIMARY = 'inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-bold bg-primary text-white hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors';

const EMPTY_FORM: UnforcedErrorCreate = {
    error_date: '', description: '', est_cost_cny: undefined, root_cause: '', linked_rule: '',
};

function cny(v: number | null | undefined): string {
    if (v == null) return '—';
    return `¥${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export const UnforcedErrors: React.FC = () => {
    const { t } = useTranslation('reports');
    const [errors, setErrors] = useState<UnforcedError[]>([]);
    const [loading, setLoading] = useState(true);
    const [fetchError, setFetchError] = useState<string | null>(null);

    // Inline cost editing
    const [editingCostId, setEditingCostId] = useState<number | null>(null);
    const [editingCostValue, setEditingCostValue] = useState<string>('');
    const [savingCost, setSavingCost] = useState(false);
    const [costError, setCostError] = useState<string | null>(null);

    // Log form
    const [showLogForm, setShowLogForm] = useState(false);
    const [form, setForm] = useState<UnforcedErrorCreate>(EMPTY_FORM);
    const [submitting, setSubmitting] = useState(false);
    const [addMsg, setAddMsg] = useState<string | null>(null);

    const load = async () => {
        setFetchError(null);
        try {
            const data = await api.getUnforcedErrors();
            setErrors(data);
        } catch (e) {
            console.error('Failed to fetch unforced errors:', e);
            setFetchError(t('unforcedErrors.errors.load'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const handleSaveCost = async (id: number) => {
        setSavingCost(true);
        setCostError(null);
        try {
            const parsed = editingCostValue.trim() === '' ? null : Number(editingCostValue);
            await api.updateUnforcedErrorCost(id, parsed);
            setEditingCostId(null);
            await load();
        } catch (err) {
            console.error('Failed to update cost:', err);
            setCostError(t('unforcedErrors.errors.updateCost'));
        } finally {
            setSavingCost(false);
        }
    };

    const handleAddError = async () => {
        if (!form.error_date || !form.description.trim()) return;
        setSubmitting(true);
        setAddMsg(null);
        try {
            await api.createUnforcedError(form);
            setForm(EMPTY_FORM);
            setShowLogForm(false);
            await load();
        } catch (e) {
            console.error('Failed to create unforced error:', e);
            setAddMsg(t('unforcedErrors.errors.saveEntry'));
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <section className={CARD}>
            {/* Header */}
            <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark">
                <h2 className="text-base font-semibold text-slate-900 dark:text-white">{t('unforcedErrors.title')}</h2>
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">{t('unforcedErrors.subtitle')}</p>
            </div>

            <div className="p-6">
                {loading ? (
                    <p className="text-sm text-slate-400 dark:text-slate-500 py-2">{t('unforcedErrors.loading')}</p>
                ) : fetchError ? (
                    <p className="text-sm text-red-600 dark:text-red-400 py-2">{fetchError}</p>
                ) : errors.length === 0 ? (
                    <p className="text-sm text-slate-400 dark:text-slate-500 py-2">{t('unforcedErrors.noneLoggedYet')}</p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-[11.5px]">
                            <thead>
                                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 dark:text-slate-400 border-b border-slate-100 dark:border-slate-800">
                                    <th className="px-3 py-2.5 font-semibold">{t('unforcedErrors.columns.date')}</th>
                                    <th className="px-3 py-2.5 font-semibold">{t('unforcedErrors.columns.description')}</th>
                                    <th className="px-3 py-2.5 font-semibold text-right">{t('unforcedErrors.columns.estCost')}</th>
                                    <th className="px-3 py-2.5 font-semibold">{t('unforcedErrors.columns.rule')}</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                                {errors.map((e) => (
                                    <tr key={e.id} className="hover:bg-slate-50/80 dark:hover:bg-white/[0.02] transition-colors">
                                        <td className="px-3 py-3 font-mono text-[11px] whitespace-nowrap text-slate-700 dark:text-slate-300">
                                            {e.error_date || '—'}
                                        </td>
                                        <td className="px-3 py-3 text-slate-700 dark:text-slate-300">{e.description}</td>
                                        <td className="px-3 py-3 font-mono text-right text-[11px]">
                                            {editingCostId === e.id ? (
                                                <span className="flex items-center gap-1 justify-end">
                                                    <input
                                                        type="number"
                                                        autoFocus
                                                        className="w-24 px-2 py-0.5 text-[11px] rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary/50"
                                                        value={editingCostValue}
                                                        onChange={(ev) => setEditingCostValue(ev.target.value)}
                                                        placeholder={t('unforcedErrors.amountPlaceholder')}
                                                    />
                                                    <button
                                                        onClick={() => handleSaveCost(e.id)}
                                                        disabled={savingCost}
                                                        className="text-[10px] px-1.5 py-0.5 rounded bg-primary text-white disabled:opacity-50"
                                                    >
                                                        {savingCost ? '…' : t('unforcedErrors.save')}
                                                    </button>
                                                    <button
                                                        onClick={() => setEditingCostId(null)}
                                                        className="text-[10px] px-1.5 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
                                                    >
                                                        {t('unforcedErrors.cancel')}
                                                    </button>
                                                </span>
                                            ) : (
                                                <span className="flex items-center gap-1 justify-end group">
                                                    <span>
                                                        {e.est_cost_cny != null
                                                            ? cny(e.est_cost_cny)
                                                            : <span className="text-slate-400 dark:text-slate-500 italic">{t('unforcedErrors.unknown')}</span>}
                                                        {(e.cost_edit_history?.length ?? 0) > 0 && (
                                                            <span
                                                                className="ml-1 text-[10px] text-slate-400 cursor-help"
                                                                title={t('unforcedErrors.editHistory', { entries: (e.cost_edit_history ?? []).map(h => `${h.ts}: ${h.old != null ? cny(h.old) : t('unforcedErrors.unknown')} → ${h.new != null ? cny(h.new) : t('unforcedErrors.unknown')}`).join('\n') })}
                                                            >
                                                                {t('unforcedErrors.editedTag')}
                                                            </span>
                                                        )}
                                                    </span>
                                                    <button
                                                        onClick={() => {
                                                            setEditingCostId(e.id);
                                                            setEditingCostValue(e.est_cost_cny != null ? String(e.est_cost_cny) : '');
                                                        }}
                                                        className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-opacity"
                                                        title={t('unforcedErrors.editEstimatedCost')}
                                                        aria-label={t('unforcedErrors.editEstimatedCost')}
                                                    >
                                                        ✏
                                                    </button>
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-3 py-3 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
                                            {e.linked_rule || '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}

                {costError && (
                    <p className="mt-2 text-[11px] text-red-600 dark:text-red-400">{costError}</p>
                )}

                {/* + Log a new entry toggle */}
                <div className="mt-4">
                    <button
                        onClick={() => {
                            setShowLogForm((v) => !v);
                            setAddMsg(null);
                        }}
                        className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline underline-offset-2 cursor-pointer"
                    >
                        <span className="material-symbols-outlined text-[15px]">{showLogForm ? 'remove' : 'add'}</span>
                        {showLogForm ? t('unforcedErrors.cancel') : t('unforcedErrors.logNewEntry')}
                    </button>
                </div>

                {showLogForm && (
                    <div className="mt-3 grid gap-2.5 items-end" style={{ gridTemplateColumns: '1fr 1.6fr 1fr auto' }}>
                        <div>
                            <label className="block text-[10px] font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('unforcedErrors.form.date')}</label>
                            <input
                                type="date"
                                className={INPUT}
                                value={form.error_date}
                                onChange={(e) => setForm({ ...form, error_date: e.target.value })}
                            />
                        </div>
                        <div>
                            <label className="block text-[10px] font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('unforcedErrors.form.description')}</label>
                            <input
                                type="text"
                                placeholder={t('unforcedErrors.form.descriptionPlaceholder')}
                                className={INPUT}
                                value={form.description}
                                onChange={(e) => setForm({ ...form, description: e.target.value })}
                            />
                        </div>
                        <div>
                            <label className="block text-[10px] font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('unforcedErrors.form.estCost')}</label>
                            <input
                                type="number"
                                placeholder={t('unforcedErrors.form.optional')}
                                className={INPUT}
                                value={form.est_cost_cny ?? ''}
                                onChange={(e) =>
                                    setForm({ ...form, est_cost_cny: e.target.value ? Number(e.target.value) : undefined })
                                }
                            />
                        </div>
                        <button
                            onClick={handleAddError}
                            disabled={submitting || !form.error_date || !form.description.trim()}
                            className={BTN_PRIMARY}
                        >
                            {submitting ? t('unforcedErrors.saving') : t('unforcedErrors.add')}
                        </button>
                    </div>
                )}
                {addMsg && (
                    <p className="text-[11px] text-red-500 dark:text-red-400 mt-2">{addMsg}</p>
                )}
            </div>
        </section>
    );
};
