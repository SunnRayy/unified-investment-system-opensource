import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TaxonomyAPI, TaxonomyClass } from '../src/services/api';

interface ClassFormData {
    name: string;
    name_cn: string;
    parent_id: number | null;
    level: number;
    sort_order: number;
    is_rebalanceable: boolean;
    description: string;
}

const defaultClassForm: ClassFormData = {
    name: '',
    name_cn: '',
    parent_id: null,
    level: 1,
    sort_order: 0,
    is_rebalanceable: true,
    description: '',
};

export const Taxonomy: React.FC = () => {
    const { t } = useTranslation('management');
    const [classes, setClasses] = useState<TaxonomyClass[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [showClassModal, setShowClassModal] = useState(false);
    const [editingClass, setEditingClass] = useState<TaxonomyClass | null>(null);
    const [classForm, setClassForm] = useState<ClassFormData>(defaultClassForm);
    const [submitting, setSubmitting] = useState(false);

    const loadData = async () => {
        setError(null);
        try {
            const classesData = await TaxonomyAPI.getClasses();
            setClasses(classesData);
        } catch (error) {
            console.error('Failed to fetch taxonomy classes', error);
            setError(t('taxonomy.loadError'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadData(); }, []);

    const openAddClass = () => {
        setEditingClass(null);
        setClassForm(defaultClassForm);
        setShowClassModal(true);
    };

    const openEditClass = (cls: TaxonomyClass) => {
        setEditingClass(cls);
        setClassForm({
            name: cls.name,
            name_cn: cls.name_cn || '',
            parent_id: cls.parent_id ?? null,
            level: cls.level,
            sort_order: cls.sort_order,
            is_rebalanceable: cls.is_rebalanceable,
            description: cls.description || '',
        });
        setShowClassModal(true);
    };

    const handleClassSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const payload = {
                ...classForm,
                parent_id: classForm.parent_id || null,
                name_cn: classForm.name_cn || null,
                description: classForm.description || null,
            };
            if (editingClass) {
                await TaxonomyAPI.updateClass(editingClass.id, payload);
            } else {
                await TaxonomyAPI.createClass(payload);
            }
            setShowClassModal(false);
            await loadData();
        } catch (error) {
            console.error('Failed to save class', error);
            alert(t('taxonomy.saveError'));
        } finally {
            setSubmitting(false);
        }
    };

    const topLevelClasses = classes;

    const rebalBadge = (v: boolean) => (
        <span className={`px-2 py-1 rounded-full text-xs font-semibold ${v ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'}`}>
            {v ? t('taxonomy.yes') : t('taxonomy.no')}
        </span>
    );

    const renderClasses = (nodes: TaxonomyClass[]) => {
        return nodes.map(node => (
            <React.Fragment key={node.id}>
                <tr className="border-b border-slate-200 dark:border-slate-700 bg-slate-100/70 dark:bg-slate-800/40">
                    <td className="px-4 py-3 text-sm font-bold text-slate-900 dark:text-slate-100">{node.name}</td>
                    <td className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">{node.name_cn || '-'}</td>
                    <td className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">{node.level}</td>
                    <td className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">{rebalBadge(node.is_rebalanceable)}</td>
                    <td className="px-4 py-3 text-sm text-right">
                        <button onClick={() => openEditClass(node)} className="text-primary hover:text-primary-dark">{t('taxonomy.edit')}</button>
                    </td>
                </tr>
                {node.children?.map(child => (
                    <tr key={child.id} className="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/50">
                        <td className="px-4 py-3 text-sm font-medium text-slate-900 dark:text-slate-100 pl-10">{child.name}</td>
                        <td className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">{child.name_cn || '-'}</td>
                        <td className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">{child.level}</td>
                        <td className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">{rebalBadge(child.is_rebalanceable)}</td>
                        <td className="px-4 py-3 text-sm text-right">
                            <button onClick={() => openEditClass(child)} className="text-primary hover:text-primary-dark">{t('taxonomy.edit')}</button>
                        </td>
                    </tr>
                ))}
            </React.Fragment>
        ));
    };

    return (
        <div className="p-8 bg-gray-50 dark:bg-background-dark min-h-screen">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('taxonomy.title')}</h1>
                    <p className="text-slate-500 dark:text-slate-400 mt-1">{t('taxonomy.subtitle')}</p>
                </div>
                <button
                    onClick={openAddClass}
                    className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark transition-colors shadow-sm shadow-primary/30"
                >
                    {t('taxonomy.addClass')}
                </button>
            </div>

            <div className="bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark overflow-hidden">
                {loading ? (
                    <div className="p-8 text-center text-slate-500">{t('taxonomy.loadingClasses')}</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">{t('taxonomy.col.name')}</th>
                                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">{t('taxonomy.col.cnName')}</th>
                                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">{t('taxonomy.col.level')}</th>
                                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">{t('taxonomy.col.rebalance')}</th>
                                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">{t('taxonomy.col.actions')}</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                {renderClasses(classes)}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Add/Edit Class Modal */}
            {showClassModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl w-full max-w-md p-6">
                        <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-4">
                            {editingClass ? t('taxonomy.editClass') : t('taxonomy.addClass')}
                        </h2>
                        <form onSubmit={handleClassSubmit} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('taxonomy.nameEnglish')}</label>
                                <input
                                    required
                                    value={classForm.name}
                                    onChange={e => setClassForm(f => ({ ...f, name: e.target.value }))}
                                    className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('taxonomy.nameChinese')}</label>
                                <input
                                    value={classForm.name_cn}
                                    onChange={e => setClassForm(f => ({ ...f, name_cn: e.target.value }))}
                                    className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('taxonomy.parentClass')}</label>
                                <select
                                    value={classForm.parent_id ?? ''}
                                    onChange={e => {
                                        const pid = e.target.value ? Number(e.target.value) : null;
                                        const autoLevel = pid ? 1 : 0;
                                        setClassForm(f => ({ ...f, parent_id: pid, level: autoLevel }));
                                    }}
                                    className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                >
                                    <option value="">{t('taxonomy.noneTopLevel')}</option>
                                    {topLevelClasses.map(c => (
                                        <option key={c.id} value={c.id} disabled={editingClass?.id === c.id}>
                                            {c.name}{c.name_cn ? ` (${c.name_cn})` : ''}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('taxonomy.col.level')}</label>
                                    <input
                                        type="number" min={0} max={3}
                                        value={classForm.level}
                                        onChange={e => setClassForm(f => ({ ...f, level: Number(e.target.value) }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('taxonomy.sortOrder')}</label>
                                    <input
                                        type="number" min={0}
                                        value={classForm.sort_order}
                                        onChange={e => setClassForm(f => ({ ...f, sort_order: Number(e.target.value) }))}
                                        className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                    />
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    id="rebalanceable"
                                    checked={classForm.is_rebalanceable}
                                    onChange={e => setClassForm(f => ({ ...f, is_rebalanceable: e.target.checked }))}
                                    className="rounded"
                                />
                                <label htmlFor="rebalanceable" className="text-sm font-medium text-slate-700 dark:text-slate-300">{t('taxonomy.rebalanceable')}</label>
                            </div>
                            <div className="flex justify-end gap-3 pt-2">
                                <button type="button" onClick={() => setShowClassModal(false)} className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white">{t('taxonomy.cancel')}</button>
                                <button type="submit" disabled={submitting} className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark disabled:opacity-50">
                                    {submitting ? t('taxonomy.saving') : (editingClass ? t('taxonomy.saveChanges') : t('taxonomy.createClass'))}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
};
