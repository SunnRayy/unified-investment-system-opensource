import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { AnalyticsAPI, Goal, GoalCreate, GoalUpdate } from '../src/services/api';
import { formatCNY } from '../src/utils/format';
import { YourPath } from '../components/forecast/YourPath';

// R-5 (docs/plans/2026-07-25-forecast-planning-redesign.md §3, §6b(e)): tabs
// collapsed 4 → 2. 'path' renders the "Your Path" narrative (W-5 rebuild,
// docs/design/2026-07-26-your-path.dc.html.md — see components/forecast/
// YourPath.tsx, which now owns all state/fetching for that tab); 'goals' is
// unchanged.
type Tab = 'path' | 'goals';

// ── Shared UI Components ───────────────────────────────────────────────────

const CARD = 'rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-card-dark shadow-sm overflow-hidden';
const INPUT_CLS = 'w-full text-sm px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors';

/** Hoisted out of the JSX child-expression container the ternary used to live
 *  in — 🏖️ is BEACH WITH UMBRELLA + a variation-selector formatting char,
 *  which isn't covered by the i18n ratchet's punctuation/symbol allowance,
 *  so it read as "prose" there. Icons, not translatable text either way. */
const GOAL_TYPE_EMOJI: Record<string, string> = {
    retirement: '🏖️',
    major_purchase: '🏠',
    education: '🎓',
};
const getGoalEmoji = (goalType: string): string => GOAL_TYPE_EMOJI[goalType] ?? '🎯';

export const Analytics: React.FC = () => {
    const { t } = useTranslation('reports');
    const [activeTab, setActiveTab] = useState<Tab>('path');
    const [error, setError] = useState<string | null>(null);

    // --- State: Goals ---
    const [goals, setGoals] = useState<Goal[]>([]);
    // probability is null when the live return/volatility/run-rate isn't
    // available (never fabricated); undefined = not loaded yet.
    const [goalProbabilities, setGoalProbabilities] = useState<Record<number, number | null>>({});
    const [isAddGoalModalOpen, setIsAddGoalModalOpen] = useState(false);
    const [newGoal, setNewGoal] = useState<GoalCreate>({
        name: '',
        target_amount: 100000,
        target_date: new Date(new Date().setFullYear(new Date().getFullYear() + 5)).toISOString().split('T')[0],
        current_amount: 0,
        monthly_contribution: 1000,
        goal_type: 'other'
    });

    // --- State: Edit Goal ---
    // current_amount / monthly_contribution are deliberately absent from
    // GoalUpdate/editForm — they are live-derived (see Goal.live), not
    // stored user intent; there is nothing here to edit.
    const [editGoalId, setEditGoalId] = useState<number | null>(null);
    const [editForm, setEditForm] = useState<GoalUpdate>({});

    // "Your Path" (W-5) owns all of its own state/fetching now — see
    // components/forecast/YourPath.tsx. This page only loads Goals-tab data.
    useEffect(() => {
        if (activeTab === 'goals' && goals.length === 0) loadGoals();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab]);

    // --- Fetchers ---
    const loadGoals = async () => {
        setError(null);
        try {
            const list = await AnalyticsAPI.listGoals();
            if (!Array.isArray(list)) {
                console.error('Expected array from listGoals, got:', list);
                setGoals([]);
                return;
            }
            setGoals(list);
            const probs: Record<number, number | null> = {};
            await Promise.all(list.map(async (g) => {
                const p = await AnalyticsAPI.getGoalProbability(g.id);
                if (p && p.goal_id !== undefined) probs[p.goal_id] = p.probability;
            }));
            setGoalProbabilities(probs);
        } catch (err) {
            console.error(err);
            setError(t('forecast.errors.load'));
            setGoals([]);
        }
    };

    // --- Handlers ---
    const handleCreateGoal = async () => {
        try {
            await AnalyticsAPI.createGoal(newGoal);
            setIsAddGoalModalOpen(false);
            setNewGoal({ ...newGoal, name: '', target_amount: 100000 });
            loadGoals();
        } catch (err) {
            console.error(err);
            alert(t('forecast.errors.createGoal'));
        }
    };

    const handleDeleteGoal = async (id: number) => {
        if (!confirm(t('forecast.confirmDeleteGoal'))) return;
        try {
            await AnalyticsAPI.deleteGoal(id);
            loadGoals();
        } catch (err) {
            console.error(err);
        }
    };

    const openEditGoal = (g: Goal) => {
        setEditForm({
            name: g.name,
            target_amount: g.target_amount,
            target_date: g.target_date,
            goal_type: g.goal_type,
            status: g.status,
            notes: g.notes || '',
        });
        setEditGoalId(g.id);
    };

    const handleUpdateGoal = async () => {
        if (editGoalId === null) return;
        try {
            await AnalyticsAPI.updateGoal(editGoalId, editForm);
            setEditGoalId(null);
            loadGoals();
        } catch (err) {
            console.error(err);
            alert(t('forecast.errors.updateGoal'));
        }
    };

    // "Your Path" (W-5, docs/design/2026-07-26-your-path.dc.html.md) is now
    // a single self-contained component — components/forecast/YourPath.tsx
    // owns every section's state/fetching. onGoToGoals lets the config-
    // fallback CTA inside AnswerSection switch tabs without a hard navigate
    // (Goals is a tab on this same page, not a separate route).
    const renderYourPath = () => <YourPath onGoToGoals={() => setActiveTab('goals')} />;

    const renderGoals = () => {
        return (
            <div className="space-y-6">
                <div className="flex justify-between items-center px-1">
                    <h3 className="text-[15px] font-bold text-slate-800 dark:text-slate-100">{t('forecast.goals.monitoredGoals')}</h3>
                    <button onClick={() => setIsAddGoalModalOpen(true)} className="px-3 py-1.5 bg-primary hover:bg-primary/90 text-white text-xs font-bold rounded-lg shadow-[0_2px_8px_-2px_rgba(59,130,246,0.5)] transition-colors flex items-center gap-1.5">
                        <span className="material-symbols-outlined text-[16px]">add</span>
                        {t('forecast.goals.newGoal')}
                    </button>
                </div>

                {goals.length === 0 ? (
                    <div className="p-16 text-center rounded-xl border border-dashed border-slate-300 dark:border-slate-700 bg-slate-50/50 dark:bg-card-dark flex flex-col items-center">
                        <div className="w-16 h-16 bg-slate-100 dark:bg-slate-800 rounded-full flex items-center justify-center mb-4 text-3xl">🎯</div>
                        <h4 className="text-base font-bold text-slate-800 dark:text-slate-200 mb-2">{t('forecast.goals.noActiveGoals')}</h4>
                        <p className="text-sm text-slate-500 max-w-sm mb-6">{t('forecast.goals.noActiveGoalsDesc')}</p>
                        <button onClick={() => setIsAddGoalModalOpen(true)} className="px-5 py-2.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 text-sm font-semibold rounded-lg shadow-sm transition-colors text-slate-700 dark:text-slate-200">
                            {t('forecast.goals.setUpAGoal')}
                        </button>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                        {goals.map(g => {
                            // Live observations — single source of truth shared with
                            // "Your Path" (fixes the owner-reported defect where the
                            // Goals card and Your Path disagreed on current/monthly).
                            // Never fall back to the legacy g.current_amount /
                            // g.monthly_contribution columns for display or math.
                            const liveCurrent = g.live.current_amount;
                            const liveMonthly = g.live.monthly_contribution; // null = unavailable
                            const prog = Math.min(100, (liveCurrent / g.target_amount) * 100);
                            const prob = goalProbabilities[g.id];
                            return (
                                <div key={g.id} className={`${CARD} flex flex-col`}>
                                    <div className="px-5 py-4 border-b border-slate-100 dark:border-slate-800/50 flex justify-between items-start bg-slate-50/50 dark:bg-slate-900/20">
                                        <div className="flex gap-3">
                                            <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center text-lg">
                                                {getGoalEmoji(g.goal_type)}
                                            </div>
                                            <div>
                                                <h4 className="font-bold text-slate-900 dark:text-white text-base leading-tight mb-1">{g.name}</h4>
                                                <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 inline-block">{g.goal_type}</span>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-0.5">
                                            <button onClick={() => openEditGoal(g)} className="text-slate-400 hover:text-primary transition-colors p-1" title={t('forecast.goals.editGoal')}>
                                                <span className="material-symbols-outlined text-[18px]">edit</span>
                                            </button>
                                            <button onClick={() => handleDeleteGoal(g.id)} className="text-slate-400 hover:text-rose-500 transition-colors p-1" title={t('forecast.goals.deleteGoal')}>
                                                <span className="material-symbols-outlined text-[18px]">delete</span>
                                            </button>
                                        </div>
                                    </div>

                                    <div className="p-5 flex-grow">
                                        <div className="grid grid-cols-2 gap-y-4 gap-x-6 mb-5">
                                            <div>
                                                <span className="text-slate-500 dark:text-slate-400 block text-[10px] font-bold uppercase tracking-wider mb-1">{t('forecast.goals.target')}</span>
                                                <span className="font-mono text-base font-bold text-slate-800 dark:text-slate-200">{formatCNY(g.target_amount)}</span>
                                            </div>
                                            <div>
                                                <span className="text-slate-500 dark:text-slate-400 block text-[10px] font-bold uppercase tracking-wider mb-1">{t('forecast.goals.current')}</span>
                                                <span className="font-mono text-base font-bold text-primary">{formatCNY(liveCurrent)}</span>
                                            </div>
                                            <div>
                                                <span className="text-slate-500 dark:text-slate-400 block text-[10px] font-bold uppercase tracking-wider mb-1">{t('forecast.goals.monthly')}</span>
                                                {liveMonthly !== null ? (
                                                    <span className="font-mono text-sm font-semibold text-slate-700 dark:text-slate-300">{formatCNY(liveMonthly)}</span>
                                                ) : (
                                                    <span className="text-slate-400 text-xs italic" title={g.live.run_rate_status}>{t('forecast.goals.unavailable')}</span>
                                                )}
                                            </div>
                                            <div>
                                                <span className="text-slate-500 dark:text-slate-400 block text-[10px] font-bold uppercase tracking-wider mb-1">{t('forecast.goals.successProb')}</span>
                                                {prob !== undefined && prob !== null ? (
                                                    <span className={`font-mono text-sm font-bold px-2 py-0.5 rounded ${prob >= 0.8 ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' : prob >= 0.5 ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400' : 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400'}`}>
                                                        {(prob * 100).toFixed(1)}%
                                                    </span>
                                                ) : prob === null ? (
                                                    <span className="text-slate-400 text-xs italic">{t('forecast.goals.unavailable')}</span>
                                                ) : (
                                                    <span className="text-slate-400 text-xs italic">{t('forecast.goals.calculating')}</span>
                                                )}
                                            </div>
                                        </div>

                                        <div className="bg-slate-50 dark:bg-slate-800/30 rounded-lg p-3">
                                            <div className="flex justify-between text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-2">
                                                <span>{t('forecast.goals.progress')} <span className="font-mono text-slate-700 dark:text-slate-300 ml-1">{prog.toFixed(1)}%</span></span>
                                                <span><span className="font-mono text-slate-700 dark:text-slate-300 mr-1">{g.months_remaining}</span> {t('forecast.goals.monthsLeft')}</span>
                                            </div>
                                            <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-1.5 overflow-hidden">
                                                <div className="bg-primary h-full rounded-full transition-all duration-700 ease-out" style={{ width: `${prog}%` }}></div>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="px-5 py-3 border-t border-slate-100 dark:border-slate-800/50 bg-slate-50/50 dark:bg-slate-900/10">
                                        {/* W-5: the what-if form this used to pre-fill (years/contribution/goal)
                                            moved into Advanced Simulation (components/forecast/
                                            AdvancedSimulation.tsx), which now owns its own internal state —
                                            no longer reachable from here. This just switches tabs; the
                                            Advanced section itself still defaults to the live run-rate. */}
                                        <button
                                            onClick={() => setActiveTab('path')}
                                            className="w-full py-2 text-primary hover:bg-primary/10 text-xs font-bold rounded-lg transition-colors flex items-center justify-center gap-1.5"
                                        >
                                            <span className="material-symbols-outlined text-[16px]">monitoring</span>
                                            {t('forecast.goals.viewYourPath')}
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}

                {/* Add Goal Modal */}
                {isAddGoalModalOpen && (
                    <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
                        <div className="bg-white dark:bg-card-dark rounded-xl shadow-2xl border border-slate-200 dark:border-slate-700 w-full max-w-md overflow-hidden flex flex-col">
                            <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex justify-between items-center bg-slate-50/50 dark:bg-slate-900/20">
                                <h3 className="text-base font-bold text-slate-900 dark:text-white">{t('forecast.goals.createNewGoal')}</h3>
                                <button onClick={() => setIsAddGoalModalOpen(false)} className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300">
                                    <span className="material-symbols-outlined">close</span>
                                </button>
                            </div>

                            <div className="p-6 space-y-4 overflow-y-auto max-h-[70vh]">
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.goalName')}</label>
                                    <input type="text" required className={INPUT_CLS} value={newGoal.name} onChange={e => setNewGoal({ ...newGoal, name: e.target.value })} placeholder={t('forecast.goals.goalNamePlaceholder')} />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.targetCny')}</label>
                                        <input type="number" className={INPUT_CLS} value={newGoal.target_amount} onChange={e => setNewGoal({ ...newGoal, target_amount: Number(e.target.value) })} />
                                    </div>
                                    <div>
                                        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.targetDate')}</label>
                                        <input type="date" className={INPUT_CLS} value={newGoal.target_date} onChange={e => setNewGoal({ ...newGoal, target_date: e.target.value })} />
                                    </div>
                                    <div>
                                        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.currentCny')}</label>
                                        <input type="number" className={INPUT_CLS} value={newGoal.current_amount} onChange={e => setNewGoal({ ...newGoal, current_amount: Number(e.target.value) })} />
                                    </div>
                                    <div>
                                        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.monthlyInputCny')}</label>
                                        <input type="number" className={INPUT_CLS} value={newGoal.monthly_contribution} onChange={e => setNewGoal({ ...newGoal, monthly_contribution: Number(e.target.value) })} />
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.category')}</label>
                                    <select className={INPUT_CLS} value={newGoal.goal_type} onChange={e => setNewGoal({ ...newGoal, goal_type: e.target.value })}>
                                        <option value="retirement">{t('forecast.goals.categories.retirement')}</option>
                                        <option value="major_purchase">{t('forecast.goals.categories.majorPurchase')}</option>
                                        <option value="education">{t('forecast.goals.categories.education')}</option>
                                        <option value="emergency_fund">{t('forecast.goals.categories.emergencyFund')}</option>
                                        <option value="other">{t('forecast.goals.categories.other')}</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.notesOptional')}</label>
                                    <textarea className={INPUT_CLS} rows={2} value={newGoal.notes || ''} onChange={e => setNewGoal({ ...newGoal, notes: e.target.value })} placeholder={t('forecast.goals.notesPlaceholder')}></textarea>
                                </div>
                            </div>

                            <div className="px-6 py-4 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/20 flex justify-end gap-3">
                                <button onClick={() => setIsAddGoalModalOpen(false)} className="px-4 py-2 text-sm font-semibold text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors">{t('forecast.goals.cancel')}</button>
                                <button onClick={handleCreateGoal} disabled={!newGoal.name} className="px-5 py-2 text-sm font-bold text-white bg-primary hover:bg-primary/90 rounded-lg shadow-[0_2px_8px_-2px_rgba(59,130,246,0.5)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed">{t('forecast.goals.saveGoal')}</button>
                            </div>
                        </div>
                    </div>
                )}

                {/* Edit Goal Modal — target_amount/target_date/name/goal_type/status/
                    notes only. current_amount/monthly_contribution are intentionally
                    absent: they are live-derived (Goal.live), not editable inputs —
                    see openEditGoal / GoalUpdate. */}
                {editGoalId !== null && (
                    <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
                        <div className="bg-white dark:bg-card-dark rounded-xl shadow-2xl border border-slate-200 dark:border-slate-700 w-full max-w-md overflow-hidden flex flex-col">
                            <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex justify-between items-center bg-slate-50/50 dark:bg-slate-900/20">
                                <h3 className="text-base font-bold text-slate-900 dark:text-white">{t('forecast.goals.editGoal')}</h3>
                                <button onClick={() => setEditGoalId(null)} className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300">
                                    <span className="material-symbols-outlined">close</span>
                                </button>
                            </div>

                            <div className="p-6 space-y-4 overflow-y-auto max-h-[70vh]">
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.goalName')}</label>
                                    <input type="text" required className={INPUT_CLS} value={editForm.name || ''} onChange={e => setEditForm({ ...editForm, name: e.target.value })} />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.targetCny')}</label>
                                        <input type="number" className={INPUT_CLS} value={editForm.target_amount ?? 0} onChange={e => setEditForm({ ...editForm, target_amount: Number(e.target.value) })} />
                                    </div>
                                    <div>
                                        <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.targetDate')}</label>
                                        <input type="date" className={INPUT_CLS} value={editForm.target_date || ''} onChange={e => setEditForm({ ...editForm, target_date: e.target.value })} />
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.category')}</label>
                                    <select className={INPUT_CLS} value={editForm.goal_type || 'other'} onChange={e => setEditForm({ ...editForm, goal_type: e.target.value })}>
                                        <option value="retirement">{t('forecast.goals.categories.retirement')}</option>
                                        <option value="major_purchase">{t('forecast.goals.categories.majorPurchase')}</option>
                                        <option value="education">{t('forecast.goals.categories.education')}</option>
                                        <option value="emergency_fund">{t('forecast.goals.categories.emergencyFund')}</option>
                                        <option value="other">{t('forecast.goals.categories.other')}</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.status')}</label>
                                    <select className={INPUT_CLS} value={editForm.status || 'active'} onChange={e => setEditForm({ ...editForm, status: e.target.value })}>
                                        <option value="active">{t('forecast.goals.statuses.active')}</option>
                                        <option value="completed">{t('forecast.goals.statuses.completed')}</option>
                                        <option value="archived">{t('forecast.goals.statuses.archived')}</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1.5">{t('forecast.goals.notesOptional')}</label>
                                    <textarea className={INPUT_CLS} rows={2} value={editForm.notes || ''} onChange={e => setEditForm({ ...editForm, notes: e.target.value })} placeholder={t('forecast.goals.notesPlaceholder')}></textarea>
                                </div>
                                <p className="text-xs text-slate-400 italic">{t('forecast.goals.liveValueDisclaimer')}</p>
                            </div>

                            <div className="px-6 py-4 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/20 flex justify-end gap-3">
                                <button onClick={() => setEditGoalId(null)} className="px-4 py-2 text-sm font-semibold text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors">{t('forecast.goals.cancel')}</button>
                                <button onClick={handleUpdateGoal} disabled={!editForm.name} className="px-5 py-2 text-sm font-bold text-white bg-primary hover:bg-primary/90 rounded-lg shadow-[0_2px_8px_-2px_rgba(59,130,246,0.5)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed">{t('forecast.goals.saveChanges')}</button>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="p-6 space-y-6 bg-gray-50 dark:bg-background-dark min-h-screen" data-testid="analytics-page">
            <div className="flex justify-between items-center mb-2">
                <div>
                    <div className="text-[10px] uppercase tracking-widest text-slate-500 dark:text-slate-400 mb-1">{t('forecast.breadcrumb')}</div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('forecast.title')}</h1>
                    <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
                        {t('forecast.subtitle')}
                    </p>
                </div>
            </div>

            {/* R-5: tabs collapsed 4 → 2 (plan §3, §6b(e)) — one page, one
                narrative. "Your Path" merges the former Projections / Cash
                Flow / North Star tabs; "Goals" is unchanged. */}
            <div className="border-b border-slate-200 dark:border-slate-800 mb-6">
                <nav className="-mb-px flex space-x-8">
                    <button
                        className={`whitespace-nowrap pb-3 px-1 border-b-2 font-bold text-sm transition-colors ${activeTab === 'path' ? 'border-primary text-primary relative z-10' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-300'}`}
                        onClick={() => setActiveTab('path')}
                    >
                        {t('forecast.tabYourPath')}
                    </button>
                    <button
                        className={`whitespace-nowrap pb-3 px-1 border-b-2 font-bold text-sm transition-colors ${activeTab === 'goals' ? 'border-primary text-primary relative z-10' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-300'}`}
                        onClick={() => setActiveTab('goals')}
                    >
                        {t('forecast.tabGoals')}
                    </button>
                </nav>
            </div>

            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <div className="animate-in fade-in duration-300">
                {activeTab === 'path' && renderYourPath()}
                {activeTab === 'goals' && renderGoals()}
            </div>
        </div>
    );
};
