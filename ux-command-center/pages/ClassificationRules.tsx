import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { TaxonomyAPI, TaxonomyRule, TaxonomyClass } from '../src/services/api';
import { useLanguage } from '../src/context/useLanguage';
import { localizedClassName } from '../src/utils/localizedClassName';

type ActiveTab = 'class' | 'tier';

interface ClassRuleFormData {
    rule_type: 'exact_id' | 'exact_name' | 'regex';
    pattern: string;
    class_id: number | '';
    priority: number;
    source: string;
}

interface TierRuleFormData {
    rule_type: 'exact_id' | 'exact_name' | 'regex';
    pattern: string;
    class_id: number | '';
    tier_id: string;
    priority: number;
    source: string;
}

interface Tier {
    id: string;
    name: string;
    name_en: string | null;
    target_pct: number;
    color: string | null;
}

const defaultClassForm: ClassRuleFormData = {
    rule_type: 'exact_id',
    pattern: '',
    class_id: '',
    priority: 50,
    source: 'manual',
};

const defaultTierForm: TierRuleFormData = {
    rule_type: 'exact_id',
    pattern: '',
    class_id: '',
    tier_id: '',
    priority: 50,
    source: 'manual',
};

export const ClassificationRules: React.FC = () => {
    const { t } = useTranslation('management');
    const { lang } = useLanguage();
    const [rules, setRules] = useState<TaxonomyRule[]>([]);
    const [classes, setClasses] = useState<TaxonomyClass[]>([]);
    const [tiers, setTiers] = useState<Tier[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState<ActiveTab>('class');
    const [error, setError] = useState<string | null>(null);

    const [showModal, setShowModal] = useState(false);
    const [classForm, setClassForm] = useState<ClassRuleFormData>(defaultClassForm);
    const [tierForm, setTierForm] = useState<TierRuleFormData>(defaultTierForm);
    const [submitting, setSubmitting] = useState(false);

    const loadData = useCallback(async () => {
        setError(null);
        try {
            const [rulesData, classesData, tiersData] = await Promise.all([
                TaxonomyAPI.getRules(),
                TaxonomyAPI.getClasses(),
                TaxonomyAPI.getTiers(),
            ]);
            setRules(rulesData || []);
            setClasses(classesData || []);
            setTiers(tiersData || []);
        } catch (error) {
            console.error('Failed to load classification rules', error);
            setError(t('classificationRules.loadError'));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    const flatClasses = classes.flatMap(c => [c, ...(c.children || [])]);

    const classRules = rules.filter(r => !r.tier_id);
    const tierRules = rules.filter(r => !!r.tier_id);

    const handleDelete = async (id: number) => {
        if (!confirm(t('classificationRules.confirmDelete'))) return;
        try {
            await TaxonomyAPI.deleteRule(id);
            loadData();
        } catch (error) {
            console.error(error);
        }
    };

    const handleAddClassRule = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!classForm.class_id) return;
        setSubmitting(true);
        try {
            await TaxonomyAPI.createRule({
                rule_type: classForm.rule_type,
                pattern: classForm.pattern,
                class_id: Number(classForm.class_id),
                priority: classForm.priority,
                source: classForm.source || 'manual',
            });
            setShowModal(false);
            setClassForm(defaultClassForm);
            await loadData();
        } catch (error) {
            console.error('Failed to create class rule', error);
            alert(`${t('classificationRules.createRuleErrorPrefix')}${error instanceof Error ? error.message : t('classificationRules.checkConsole')}`);
        } finally {
            setSubmitting(false);
        }
    };

    const handleAddTierRule = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!tierForm.class_id || !tierForm.tier_id) return;
        setSubmitting(true);
        try {
            await TaxonomyAPI.createRule({
                rule_type: tierForm.rule_type,
                pattern: tierForm.pattern,
                class_id: Number(tierForm.class_id),
                tier_id: tierForm.tier_id,
                priority: tierForm.priority,
                source: tierForm.source || 'manual',
            });
            setShowModal(false);
            setTierForm(defaultTierForm);
            await loadData();
        } catch (error) {
            console.error('Failed to create tier rule', error);
            alert(`${t('classificationRules.createRuleErrorPrefix')}${error instanceof Error ? error.message : t('classificationRules.checkConsole')}`);
        } finally {
            setSubmitting(false);
        }
    };

    const getBadgeColor = (type: string) => {
        switch (type) {
            case 'exact_id': return 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300';
            case 'exact_name': return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300';
            case 'regex': return 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300';
            default: return 'bg-slate-100 text-slate-800';
        }
    };

    const patternPlaceholder = (type: string) =>
        type === 'exact_id' ? 'e.g. US_STK_AAPL' : type === 'regex' ? 'e.g. ^INS_' : 'e.g. Apple Inc';

    const sharedRuleColumns = (
        <thead className="bg-slate-50 dark:bg-surface-dark border-b border-slate-200 dark:border-border-dark">
            <tr>
                <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase">{t('classificationRules.col.priority')}</th>
                <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase">{t('classificationRules.col.type')}</th>
                <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase">{t('classificationRules.col.pattern')}</th>
                <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase">{t('classificationRules.col.arrowClass')}</th>
                {activeTab === 'tier' && <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase">{t('classificationRules.col.arrowTier')}</th>}
                <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase">{t('classificationRules.col.source')}</th>
                <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase"></th>
            </tr>
        </thead>
    );

    const renderRows = (displayRules: TaxonomyRule[]) => {
        if (loading) return <tr><td colSpan={7} className="p-8 text-center text-slate-500">{t('classificationRules.loadingEllipsis')}</td></tr>;
        if (displayRules.length === 0) return <tr><td colSpan={7} className="p-8 text-center text-slate-500">{t('classificationRules.noRulesFound')}</td></tr>;
        return displayRules.map(rule => (
            <tr key={rule.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <td className="px-6 py-4 text-sm font-mono text-slate-500">{rule.priority}</td>
                <td className="px-6 py-4">
                    <span className={`px-2 py-1 rounded-full text-xs font-bold ${getBadgeColor(rule.rule_type)}`}>
                        {rule.rule_type}
                    </span>
                </td>
                <td className="px-6 py-4 text-sm font-mono text-slate-900 dark:text-slate-100">{rule.pattern}</td>
                <td className="px-6 py-4 text-sm text-slate-700 dark:text-slate-300">{rule.class_name ? localizedClassName(rule.class_name, rule.class_name_cn, lang) : rule.class_id}</td>
                {activeTab === 'tier' && (
                    <td className="px-6 py-4 text-sm text-slate-700 dark:text-slate-300">{rule.tier_name || rule.tier_id || '—'}</td>
                )}
                <td className="px-6 py-4 text-xs text-slate-400">{rule.source || '—'}</td>
                <td className="px-6 py-4">
                    <button onClick={() => handleDelete(rule.id)} className="text-red-600 hover:text-red-800 text-xs font-medium">
                        {t('classificationRules.delete')}
                    </button>
                </td>
            </tr>
        ));
    };

    return (
        <div className="p-8 bg-gray-50 dark:bg-background-dark min-h-screen">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <div className="flex justify-between items-center mb-6">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('classificationRules.title')}</h1>
                    <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                        {t('classificationRules.subtitle')}
                    </p>
                </div>
                <button
                    onClick={() => {
                        setClassForm(defaultClassForm);
                        setTierForm(defaultTierForm);
                        setShowModal(true);
                    }}
                    className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover font-medium text-sm"
                >
                    {t('classificationRules.addRule')}
                </button>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 mb-4 border-b border-slate-200 dark:border-border-dark">
                <button
                    onClick={() => setActiveTab('class')}
                    className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${activeTab === 'class'
                        ? 'border-primary text-primary'
                        : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                    {t('classificationRules.classRules')}
                    <span className="ml-2 px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 text-xs">{classRules.length}</span>
                </button>
                <button
                    onClick={() => setActiveTab('tier')}
                    className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${activeTab === 'tier'
                        ? 'border-primary text-primary'
                        : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                >
                    {t('classificationRules.tierRules')}
                    <span className="ml-2 px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 text-xs">{tierRules.length}</span>
                </button>
            </div>

            <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark shadow-sm overflow-hidden">
                <table className="w-full text-left border-collapse">
                    {sharedRuleColumns}
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                        {renderRows(activeTab === 'class' ? classRules : tierRules)}
                    </tbody>
                </table>
            </div>

            {/* Add Rule Modal */}
            {showModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl w-full max-w-md p-6">
                        <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-4">
                            {activeTab === 'class' ? t('classificationRules.addClassRule') : t('classificationRules.addTierRule')}
                        </h2>

                        {activeTab === 'class' ? (
                            <form onSubmit={handleAddClassRule} className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.ruleType')}</label>
                                    <select
                                        value={classForm.rule_type}
                                        onChange={e => setClassForm(f => ({ ...f, rule_type: e.target.value as ClassRuleFormData['rule_type'] }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    >
                                        <option value="exact_id">{t('classificationRules.ruleTypeOption.exactId')}</option>
                                        <option value="exact_name">{t('classificationRules.ruleTypeOption.exactName')}</option>
                                        <option value="regex">{t('classificationRules.ruleTypeOption.regex')}</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.pattern')}</label>
                                    <input
                                        required
                                        value={classForm.pattern}
                                        onChange={e => setClassForm(f => ({ ...f, pattern: e.target.value }))}
                                        placeholder={patternPlaceholder(classForm.rule_type)}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.assetClass')}</label>
                                    <select
                                        required
                                        value={classForm.class_id}
                                        onChange={e => setClassForm(f => ({ ...f, class_id: e.target.value ? Number(e.target.value) : '' }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    >
                                        <option value="">{t('classificationRules.selectClassEllipsis')}</option>
                                        {flatClasses.map(c => (
                                            <option key={c.id} value={c.id}>{c.name}{c.name_cn ? ` (${c.name_cn})` : ''}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.priority')}</label>
                                        <input
                                            type="number" min={1} max={1000}
                                            value={classForm.priority}
                                            onChange={e => setClassForm(f => ({ ...f, priority: Number(e.target.value) }))}
                                            className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.source')}</label>
                                        <input
                                            value={classForm.source}
                                            onChange={e => setClassForm(f => ({ ...f, source: e.target.value }))}
                                            placeholder={t('classificationRules.manualPlaceholder')}
                                            className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                        />
                                    </div>
                                </div>
                                <div className="flex justify-end gap-3 pt-2">
                                    <button type="button" onClick={() => setShowModal(false)} className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-900">{t('classificationRules.cancel')}</button>
                                    <button type="submit" disabled={submitting} className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark disabled:opacity-50">
                                        {submitting ? t('classificationRules.creatingEllipsis') : t('classificationRules.createRule')}
                                    </button>
                                </div>
                            </form>
                        ) : (
                            <form onSubmit={handleAddTierRule} className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.ruleType')}</label>
                                    <select
                                        value={tierForm.rule_type}
                                        onChange={e => setTierForm(f => ({ ...f, rule_type: e.target.value as TierRuleFormData['rule_type'] }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    >
                                        <option value="exact_id">{t('classificationRules.ruleTypeOption.exactId')}</option>
                                        <option value="exact_name">{t('classificationRules.ruleTypeOption.exactName')}</option>
                                        <option value="regex">{t('classificationRules.ruleTypeOption.regex')}</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.pattern')}</label>
                                    <input
                                        required
                                        value={tierForm.pattern}
                                        onChange={e => setTierForm(f => ({ ...f, pattern: e.target.value }))}
                                        placeholder={patternPlaceholder(tierForm.rule_type)}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.assetClass')}</label>
                                    <select
                                        required
                                        value={tierForm.class_id}
                                        onChange={e => setTierForm(f => ({ ...f, class_id: e.target.value ? Number(e.target.value) : '' }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    >
                                        <option value="">{t('classificationRules.selectClassEllipsis')}</option>
                                        {flatClasses.map(c => (
                                            <option key={c.id} value={c.id}>{c.name}{c.name_cn ? ` (${c.name_cn})` : ''}</option>
                                        ))}
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.tier')}</label>
                                    <select
                                        required
                                        value={tierForm.tier_id}
                                        onChange={e => setTierForm(f => ({ ...f, tier_id: e.target.value }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    >
                                        <option value="">{t('classificationRules.selectTierEllipsis')}</option>
                                        {tiers.map(tr => (
                                            <option key={tr.id} value={tr.id}>{tr.name}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.priority')}</label>
                                        <input
                                            type="number" min={1} max={1000}
                                            value={tierForm.priority}
                                            onChange={e => setTierForm(f => ({ ...f, priority: Number(e.target.value) }))}
                                            className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('classificationRules.source')}</label>
                                        <input
                                            value={tierForm.source}
                                            onChange={e => setTierForm(f => ({ ...f, source: e.target.value }))}
                                            placeholder={t('classificationRules.manualPlaceholder')}
                                            className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                        />
                                    </div>
                                </div>
                                <div className="flex justify-end gap-3 pt-2">
                                    <button type="button" onClick={() => setShowModal(false)} className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-900">{t('classificationRules.cancel')}</button>
                                    <button type="submit" disabled={submitting} className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark disabled:opacity-50">
                                        {submitting ? t('classificationRules.creatingEllipsis') : t('classificationRules.createRule')}
                                    </button>
                                </div>
                            </form>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};
