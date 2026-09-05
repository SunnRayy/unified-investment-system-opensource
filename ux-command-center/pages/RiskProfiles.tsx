import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RiskProfileAPI, RiskProfile, RiskAllocation, TaxonomyAPI, TaxonomyClass } from '../src/services/api';
import { useLanguage } from '../src/context/useLanguage';
import { localizedClassName } from '../src/utils/localizedClassName';

export const RiskProfiles: React.FC = () => {
    const { t } = useTranslation(['management', 'common']);
    const { lang } = useLanguage();
    const [profiles, setProfiles] = useState<RiskProfile[]>([]);
    const [allocations, setAllocations] = useState<RiskAllocation[]>([]);
    const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Create Profile modal
    const [showCreateProfile, setShowCreateProfile] = useState(false);
    const [profileForm, setProfileForm] = useState({ name: '', description: '' });
    const [creatingProfile, setCreatingProfile] = useState(false);

    // Edit Targets modal
    const [showEditTargets, setShowEditTargets] = useState(false);
    const [taxonomyClasses, setTaxonomyClasses] = useState<TaxonomyClass[]>([]);
    const [targetEdits, setTargetEdits] = useState<Record<number, string>>({});
    const [savingTargets, setSavingTargets] = useState(false);

    useEffect(() => {
        loadProfiles();
        // Load taxonomy at startup for main view grouping and edit modal
        TaxonomyAPI.getClasses().then(setTaxonomyClasses).catch(console.error);
    }, []);

    useEffect(() => {
        if (selectedProfileId) {
            loadAllocations(selectedProfileId);
        } else {
            setAllocations([]);
        }
    }, [selectedProfileId]);

    const loadProfiles = async () => {
        setError(null);
        try {
            const data = await RiskProfileAPI.getProfiles();
            setProfiles(data);
            if (data.length > 0 && !selectedProfileId) {
                setSelectedProfileId(data[0].id);
            }
        } catch (error) {
            console.error("Failed to load profiles", error);
            setError(t('riskProfiles.loadError'));
        } finally {
            setLoading(false);
        }
    };

    const loadAllocations = async (id: number) => {
        setError(null);
        try {
            const data = await RiskProfileAPI.getAllocations(id);
            setAllocations(data);
        } catch (error) {
            console.error("Failed to load allocations", error);
            setError(t('riskProfiles.loadError'));
        }
    };

    const handleActivate = async (id: number) => {
        try {
            await RiskProfileAPI.activateProfile(id);
            await loadProfiles();
        } catch (error) {
            console.error("Failed to activate profile", error);
        }
    };

    const handleCreateProfile = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreatingProfile(true);
        try {
            const result = await RiskProfileAPI.createProfile({
                name: profileForm.name,
                description: profileForm.description || null,
                is_active: false,
            });
            setShowCreateProfile(false);
            setProfileForm({ name: '', description: '' });
            await loadProfiles();
            setSelectedProfileId(result.id);
        } catch (error) {
            console.error("Failed to create profile", error);
            alert(t('riskProfiles.createError'));
        } finally {
            setCreatingProfile(false);
        }
    };

    const openEditTargets = async () => {
        try {
            // Fetch taxonomy if not already loaded
            let classes = taxonomyClasses;
            if (classes.length === 0) {
                classes = await TaxonomyAPI.getClasses();
                setTaxonomyClasses(classes);
            }
            // Only pre-fill level-1 (sub-class) allocations — level-0 seeded entries
            // have class IDs that don't appear in the sub-class form rows and would
            // accumulate as hidden values, inflating the total counter.
            const level1Ids = new Set(classes.flatMap(c => (c.children || []).map(ch => ch.id)));
            const current: Record<number, string> = {};
            for (const alloc of allocations) {
                if (level1Ids.has(alloc.class_id)) {
                    current[alloc.class_id] = String(alloc.target_pct);
                }
            }
            setTargetEdits(current);
            setShowEditTargets(true);
        } catch (error) {
            console.error("Failed to load classes for edit", error);
        }
    };

    const handleSaveTargets = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!selectedProfileId) return;
        setSavingTargets(true);
        try {
            const allocationsMap: Record<number, number> = {};
            for (const [classId, pct] of Object.entries(targetEdits)) {
                const val = parseFloat(pct);
                if (!isNaN(val) && val > 0) {
                    allocationsMap[Number(classId)] = val;
                }
            }
            await RiskProfileAPI.updateAllocations(selectedProfileId, allocationsMap);
            setShowEditTargets(false);
            await loadAllocations(selectedProfileId);
        } catch (error) {
            console.error("Failed to save targets", error);
            alert(t('riskProfiles.saveTargetsError'));
        } finally {
            setSavingTargets(false);
        }
    };

    const totalTarget = Object.values(targetEdits).reduce((sum, v) => sum + (parseFloat(v) || 0), 0);

    // Group allocations by parent class for main view display
    const allocationsByParent = useMemo(() => {
        if (!taxonomyClasses.length || !allocations.length) return null;
        const classInfo = new Map<number, { level: number; parent_id: number | null; parent_name: string; parent_name_cn: string | null | undefined }>();
        for (const p of taxonomyClasses) {
            classInfo.set(p.id, { level: 0, parent_id: null, parent_name: '', parent_name_cn: null });
            for (const c of p.children || []) {
                classInfo.set(c.id, { level: 1, parent_id: p.id, parent_name: p.name, parent_name_cn: p.name_cn });
            }
        }
        // Group by parent_id; level-0 or unknown allocations go into a null group
        const groups = new Map<number | null, { name: string; name_cn: string | null | undefined; items: RiskAllocation[] }>();
        for (const alloc of allocations) {
            const info = classInfo.get(alloc.class_id);
            if (info?.level === 1 && info.parent_id !== null) {
                if (!groups.has(info.parent_id)) {
                    groups.set(info.parent_id, { name: info.parent_name, name_cn: info.parent_name_cn, items: [] });
                }
                groups.get(info.parent_id)!.items.push(alloc);
            } else {
                if (!groups.has(null)) groups.set(null, { name: '', name_cn: null, items: [] });
                groups.get(null)!.items.push(alloc);
            }
        }
        return groups;
    }, [allocations, taxonomyClasses]);

    return (
        <div className="p-8 bg-gray-50 dark:bg-background-dark min-h-screen">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('common:nav.riskProfiles')}</h1>
                    <p className="text-slate-500 dark:text-slate-400 mt-1">{t('riskProfiles.subtitle')}</p>
                </div>
                <button
                    onClick={() => { setProfileForm({ name: '', description: '' }); setShowCreateProfile(true); }}
                    className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark transition-colors shadow-sm shadow-primary/30"
                >
                    {t('riskProfiles.createProfile')}
                </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {/* Profile List */}
                <div className="md:col-span-1 bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark overflow-hidden">
                    <div className="p-4 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50">
                        <h2 className="font-semibold text-slate-900 dark:text-slate-100">{t('riskProfiles.profiles')}</h2>
                    </div>
                    <div className="divide-y divide-slate-100 dark:divide-slate-800">
                        {loading ? (
                            <div className="p-4 text-center text-slate-500">{t('riskProfiles.loadingEllipsis')}</div>
                        ) : (
                            profiles.map(profile => (
                                <div
                                    key={profile.id}
                                    onClick={() => setSelectedProfileId(profile.id)}
                                    className={`p-4 cursor-pointer transition-colors ${selectedProfileId === profile.id ? 'bg-primary/5 border-l-4 border-primary' : 'hover:bg-slate-50 dark:hover:bg-slate-800/50 border-l-4 border-transparent'}`}
                                >
                                    <div className="flex justify-between items-start">
                                        <div>
                                            <h3 className={`font-medium ${selectedProfileId === profile.id ? 'text-primary' : 'text-slate-900 dark:text-slate-100'}`}>
                                                {profile.name}
                                            </h3>
                                            <p className="text-xs text-slate-500 mt-1">{profile.description || t('riskProfiles.noDescription')}</p>
                                        </div>
                                        {profile.is_active && (
                                            <span className="px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-[10px] font-bold uppercase tracking-wide">
                                                {t('riskProfiles.active')}
                                            </span>
                                        )}
                                    </div>
                                    {!profile.is_active && selectedProfileId === profile.id && (
                                        <button
                                            onClick={(e) => { e.stopPropagation(); handleActivate(profile.id); }}
                                            className="mt-3 text-xs font-semibold text-primary hover:text-primary-dark"
                                        >
                                            {t('riskProfiles.setAsActive')}
                                        </button>
                                    )}
                                </div>
                            ))
                        )}
                    </div>
                </div>

                {/* Allocation Details */}
                <div className="md:col-span-2 bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark overflow-hidden">
                    <div className="p-4 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 flex justify-between items-center">
                        <h2 className="font-semibold text-slate-900 dark:text-slate-100">{t('riskProfiles.targetAllocations')}</h2>
                        {selectedProfileId && (
                            <button
                                onClick={openEditTargets}
                                className="text-sm text-primary hover:text-primary-dark font-medium"
                            >
                                {t('riskProfiles.editTargets')}
                            </button>
                        )}
                    </div>

                    {selectedProfileId ? (
                        <div className="p-6">
                            <div className="mb-6">
                                <div className="flex h-4 w-full rounded-full overflow-hidden bg-slate-100 dark:bg-slate-800">
                                    {allocations.map((alloc, idx) => (
                                        <div
                                            key={alloc.class_id}
                                            style={{ width: `${alloc.target_pct}%`, backgroundColor: `hsl(${idx * 60}, 70%, 50%)` }}
                                            title={`${localizedClassName(alloc.class_name, alloc.class_name_cn, lang)}: ${alloc.target_pct}%`}
                                        />
                                    ))}
                                </div>
                            </div>

                            <table className="w-full text-left border-collapse">
                                <thead>
                                    <tr className="border-b border-slate-200 dark:border-slate-700">
                                        <th className="px-4 py-2 text-xs font-semibold text-slate-500 uppercase">{t('riskProfiles.col.assetClass')}</th>
                                        <th className="px-4 py-2 text-xs font-semibold text-slate-500 uppercase text-right">{t('riskProfiles.col.targetPct')}</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                    {allocationsByParent ? (
                                        [...allocationsByParent.entries()].map(([parentId, group]) => (
                                            <React.Fragment key={parentId ?? 'ungrouped'}>
                                                {parentId !== null && group.items.length > 0 && (
                                                    <tr className="bg-slate-50 dark:bg-slate-800/30">
                                                        <td className="px-4 py-2 text-xs font-semibold text-slate-500 uppercase tracking-wide">{localizedClassName(group.name, group.name_cn, lang)}</td>
                                                        <td className="px-4 py-2 text-xs font-semibold text-slate-500 text-right">
                                                            {group.items.reduce((s, a) => s + a.target_pct, 0).toFixed(1)}%
                                                        </td>
                                                    </tr>
                                                )}
                                                {group.items.map(alloc => (
                                                    <tr key={alloc.class_id}>
                                                        <td className={`py-2.5 text-sm font-medium text-slate-900 dark:text-slate-100 ${parentId !== null ? 'pl-8 pr-4' : 'px-4'}`}>{localizedClassName(alloc.class_name, alloc.class_name_cn, lang)}</td>
                                                        <td className="px-4 py-2.5 text-sm text-slate-600 dark:text-slate-300 text-right">{alloc.target_pct.toFixed(1)}%</td>
                                                    </tr>
                                                ))}
                                            </React.Fragment>
                                        ))
                                    ) : (
                                        allocations.map(alloc => (
                                            <tr key={alloc.class_id}>
                                                <td className="px-4 py-3 text-sm font-medium text-slate-900 dark:text-slate-100">{localizedClassName(alloc.class_name, alloc.class_name_cn, lang)}</td>
                                                <td className="px-4 py-3 text-sm text-slate-600 dark:text-slate-300 text-right">{alloc.target_pct.toFixed(1)}%</td>
                                            </tr>
                                        ))
                                    )}
                                    <tr className="bg-slate-50 dark:bg-slate-800/50 font-bold">
                                        <td className="px-4 py-3 text-sm text-slate-900 dark:text-slate-100">{t('riskProfiles.total')}</td>
                                        <td className="px-4 py-3 text-sm text-slate-900 dark:text-slate-100 text-right">
                                            {allocations.reduce((sum, a) => sum + a.target_pct, 0).toFixed(1)}%
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <div className="p-12 text-center text-slate-400">
                            {t('riskProfiles.selectProfileHint')}
                        </div>
                    )}
                </div>
            </div>

            {/* Create Profile Modal */}
            {showCreateProfile && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl w-full max-w-md p-6">
                        <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-4">{t('riskProfiles.createRiskProfile')}</h2>
                        <form onSubmit={handleCreateProfile} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('riskProfiles.profileName')}</label>
                                <input
                                    required
                                    value={profileForm.name}
                                    onChange={e => setProfileForm(f => ({ ...f, name: e.target.value }))}
                                    placeholder={t('riskProfiles.profileNamePlaceholder')}
                                    className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">{t('riskProfiles.descriptionOptional')}</label>
                                <textarea
                                    value={profileForm.description}
                                    onChange={e => setProfileForm(f => ({ ...f, description: e.target.value }))}
                                    rows={3}
                                    className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                />
                            </div>
                            <div className="flex justify-end gap-3 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowCreateProfile(false)}
                                    className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
                                >
                                    {t('riskProfiles.cancel')}
                                </button>
                                <button
                                    type="submit"
                                    disabled={creatingProfile}
                                    className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark disabled:opacity-50"
                                >
                                    {creatingProfile ? t('riskProfiles.creatingEllipsis') : t('riskProfiles.createProfile')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Edit Targets Modal */}
            {showEditTargets && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl w-full max-w-lg p-6 max-h-[80vh] flex flex-col">
                        <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-1">{t('riskProfiles.editTargetAllocations')}</h2>
                        <p className="text-sm text-slate-500 mb-4">{t('riskProfiles.editTargetsHint')}</p>
                        <form onSubmit={handleSaveTargets} className="flex flex-col flex-1 overflow-hidden">
                            <div className="overflow-y-auto flex-1 pr-1">
                                {taxonomyClasses.map(parent => (
                                    (parent.children || []).length > 0 && (
                                        <div key={parent.id} className="mb-3">
                                            <div className="px-1 pt-2 pb-1 text-xs font-semibold text-slate-400 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800 mb-1">{localizedClassName(parent.name, parent.name_cn, lang)}</div>
                                            {(parent.children || []).map(cls => (
                                                <div key={cls.id} className="flex items-center gap-3 py-1 pl-3">
                                                    <span className="flex-1 text-sm text-slate-700 dark:text-slate-300">{localizedClassName(cls.name, cls.name_cn, lang)}</span>
                                                    <div className="flex items-center gap-1">
                                                        <input
                                                            type="number" min={0} max={100} step={0.1}
                                                            value={targetEdits[cls.id] ?? '0'}
                                                            onChange={e => setTargetEdits(prev => ({ ...prev, [cls.id]: e.target.value }))}
                                                            className="w-20 px-2 py-1 border border-slate-200 dark:border-slate-700 rounded text-sm text-right bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100"
                                                        />
                                                        <span className="text-sm text-slate-500">%</span>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )
                                ))}
                            </div>
                            <div className={`mt-4 pt-3 border-t border-slate-200 dark:border-slate-700 flex justify-between items-center`}>
                                <span className={`text-sm font-semibold ${Math.abs(totalTarget - 100) < 0.1 ? 'text-green-600' : 'text-amber-600'}`}>
                                    {t('riskProfiles.totalPct', { pct: totalTarget.toFixed(1) })}
                                    {Math.abs(totalTarget - 100) >= 0.1 && ` ${t('riskProfiles.shouldBe100')}`}
                                </span>
                                <div className="flex gap-3">
                                    <button
                                        type="button"
                                        onClick={() => setShowEditTargets(false)}
                                        className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
                                    >
                                        {t('riskProfiles.cancel')}
                                    </button>
                                    <button
                                        type="submit"
                                        disabled={savingTargets}
                                        className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark disabled:opacity-50"
                                    >
                                        {savingTargets ? t('riskProfiles.savingEllipsis') : t('riskProfiles.saveTargets')}
                                    </button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
};
