import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { TaxonomyAPI } from '../src/services/api';
import { useLanguage } from '../src/context/useLanguage';
import { formatCNY } from '../src/utils/format';
import { localizedClassName } from '../src/utils/localizedClassName';

interface Tier {
    id: string;
    name: string;
    name_en: string | null;
    target_pct: number;
    color: string | null;
}

interface AuditAsset {
    asset_id: string;
    asset_name: string;
    asset_class: string | null;
    tier: string | null;
    source_system: string;
    class_name: string | null;
    class_name_cn: string | null;
    parent_class_name: string | null;
    parent_class_name_cn: string | null;
    market_value_cny: number | null;
    is_rebalanceable: boolean;
}

interface TierModalState {
    asset: AuditAsset;
    selectedTierId: string;
}

export const TierAudit: React.FC = () => {
    const { t } = useTranslation(['management', 'common']);
    const { lang } = useLanguage();
    const [tiers, setTiers] = useState<Tier[]>([]);
    const [assets, setAssets] = useState<AuditAsset[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
    const [autoTagMsg, setAutoTagMsg] = useState<string | null>(null);
    const [tierModal, setTierModal] = useState<TierModalState | null>(null);
    const [tierModalSaving, setTierModalSaving] = useState(false);

    const load = useCallback(async () => {
        setError(null);
        try {
            const [tierList, auditData] = await Promise.all([
                TaxonomyAPI.getTiers(),
                TaxonomyAPI.getAssetAudit()
            ]);
            const loadedTiers = tierList || [];
            setTiers(loadedTiers);
            setAssets(auditData.assets || []);
            // Default: expand all tier groups
            const groups = new Set(loadedTiers.map((t: Tier) => t.name));
            groups.add('Unassigned');
            setExpandedGroups(groups);
        } catch (error) {
            console.error("Failed to load tier audit", error);
            setError(t('tierAudit.loadError'));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => { load(); }, [load]);

    const toggleExpand = (id: string) => {
        setExpandedGroups(prev => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            return next;
        });
    };

    const handleAutoTag = async () => {
        try {
            setAutoTagMsg(null);
            const result = await TaxonomyAPI.runAutoTag();
            setAutoTagMsg(t('tierAudit.autoAssignedMsg', { count: result.classified ?? 0 }));
            await load();
        } catch (err) {
            console.error('Auto-tag failed', err);
            setAutoTagMsg(t('tierAudit.autoAssignFailed'));
        }
    };

    const openTierModal = (asset: AuditAsset) => {
        const currentTier = tiers.find(t => t.name === asset.tier);
        setTierModal({ asset, selectedTierId: currentTier?.id ?? '' });
    };

    const handleTierSave = async () => {
        if (!tierModal) return;
        setTierModalSaving(true);
        try {
            await TaxonomyAPI.setAssetTier(
                tierModal.asset.asset_id,
                tierModal.selectedTierId || null
            );
            setTierModal(null);
            await load();
        } catch (err) {
            console.error('Set tier failed', err);
        } finally {
            setTierModalSaving(false);
        }
    };

    // Only rebalanceable assets count toward tier allocation percentages
    const rebalAssets = assets.filter(a => a.is_rebalanceable !== false);
    const totalValue = rebalAssets.reduce((sum, a) => sum + (a.market_value_cny || 0), 0);

    // Group assets by tier name (asset_registry.tier stores the full Chinese tier name)
    const groupedByTier: Record<string, AuditAsset[]> = {};
    for (const asset of assets) {
        const group = asset.tier || 'Unassigned';
        if (!groupedByTier[group]) groupedByTier[group] = [];
        groupedByTier[group].push(asset);
    }

    // Calculate current allocation per tier — only rebalanceable assets
    const tierAllocations = tiers.map(tier => {
        const matchingAssets = rebalAssets.filter(a => a.tier === tier.name);
        const tierValue = matchingAssets.reduce((sum, a) => sum + (a.market_value_cny || 0), 0);
        const currentPct = totalValue > 0 ? (tierValue / totalValue) * 100 : 0;
        const drift = currentPct - tier.target_pct;
        return { ...tier, currentPct, drift, tierValue, assetCount: matchingAssets.length };
    });

    const getDriftColor = (drift: number) => {
        const absDrift = Math.abs(drift);
        if (absDrift <= 5) return 'text-emerald-600 dark:text-emerald-400';
        if (absDrift <= 10) return 'text-amber-600 dark:text-amber-400';
        return 'text-red-600 dark:text-red-400';
    };

    // Ordered groups: tier names in sort order, then Unassigned last
    const orderedGroups = [
        ...tiers.map(t => t.name).filter(name => groupedByTier[name]),
        ...(groupedByTier['Unassigned'] ? ['Unassigned'] : []),
    ];

    if (loading) return <div className="p-8 text-slate-500 dark:text-slate-400">{t('tierAudit.loading')}</div>;

    return (
        <div className="p-8 bg-gray-50 dark:bg-background-dark min-h-screen">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <header className="mb-6 flex items-start justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('common:nav.tierAudit')}</h1>
                    <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                        {t('tierAudit.subtitle', { count: rebalAssets.length, total: formatCNY(totalValue) })}
                    </p>
                    {autoTagMsg && (
                        <p className="text-xs text-emerald-600 mt-1">{autoTagMsg}</p>
                    )}
                </div>
                <button
                    onClick={handleAutoTag}
                    aria-label={t('tierAudit.autoAssignAria')}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium transition-colors"
                >
                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">auto_fix_high</span>
                    {t('tierAudit.autoAssignTiers')}
                </button>
            </header>

            {/* Tier Summary Cards */}
            <div className="grid grid-cols-3 gap-4 mb-6">
                {tierAllocations.map(tier => (
                    <div key={tier.id} className="p-4 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                        <div className="flex items-center gap-2 mb-2">
                            {tier.color && <span className="w-3 h-3 rounded-full" style={{ backgroundColor: tier.color }} />}
                            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{tier.name}</p>
                        </div>
                        <div className="flex items-baseline gap-3">
                            <div>
                                <p className="text-xs text-slate-400">{t('tierAudit.target')}</p>
                                <p className="text-2xl font-mono font-bold text-slate-900 dark:text-white">{tier.target_pct}%</p>
                            </div>
                            <div>
                                <p className="text-xs text-slate-400">{t('tierAudit.current')}</p>
                                <p className="text-2xl font-mono font-bold text-slate-700 dark:text-slate-300">{tier.currentPct.toFixed(1)}%</p>
                            </div>
                            <div>
                                <p className="text-xs text-slate-400">{t('tierAudit.drift')}</p>
                                <p className={`text-lg font-mono font-bold ${getDriftColor(tier.drift)}`}>
                                    {tier.drift >= 0 ? '+' : ''}{tier.drift.toFixed(1)}%
                                </p>
                            </div>
                        </div>
                        <p className="text-xs text-slate-400 mt-2">{t('tierAudit.assetsCountValue', { count: tier.assetCount, value: formatCNY(tier.tierValue) })}</p>
                    </div>
                ))}
            </div>

            {/* Assets grouped by tier */}
            <div className="space-y-4">
                {orderedGroups.map(group => {
                    const groupAssets = groupedByTier[group] || [];
                    const groupValue = groupAssets.reduce((sum, a) => sum + (a.market_value_cny || 0), 0);
                    const isExpanded = expandedGroups.has(group);
                    const tier = tiers.find(tr => tr.name === group);
                    const groupLabel = group === 'Unassigned' ? t('tierAudit.unassigned') : group;
                    return (
                        <div key={group} className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark shadow-sm overflow-hidden">
                            <button
                                onClick={() => toggleExpand(group)}
                                className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-surface-dark hover:bg-slate-100 dark:hover:bg-slate-700/50 transition-colors"
                            >
                                <div className="flex items-center gap-2">
                                    <span className="material-symbols-outlined text-[18px] text-slate-400">
                                        {isExpanded ? 'expand_more' : 'chevron_right'}
                                    </span>
                                    {tier?.color && <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: tier.color }} />}
                                    <span className="text-sm font-bold text-slate-900 dark:text-white">{groupLabel}</span>
                                    <span className="text-xs text-slate-400">{t('tierAudit.assetsCount', { count: groupAssets.length })}</span>
                                </div>
                                <span className="text-sm font-mono text-slate-600 dark:text-slate-300">{formatCNY(groupValue)}</span>
                            </button>
                            {isExpanded && (
                                <table className="w-full text-left border-collapse">
                                    <thead>
                                        <tr className="border-b border-slate-100 dark:border-border-dark">
                                            <th className="px-4 py-2 text-[11px] font-bold text-slate-500 uppercase">{t('tierAudit.col.assetId')}</th>
                                            <th className="px-4 py-2 text-[11px] font-bold text-slate-500 uppercase">{t('tierAudit.col.name')}</th>
                                            <th className="px-4 py-2 text-[11px] font-bold text-slate-500 uppercase">{t('tierAudit.col.class')}</th>
                                            <th className="px-4 py-2 text-[11px] font-bold text-slate-500 uppercase text-right">{t('tierAudit.col.marketValue')}</th>
                                            <th className="px-4 py-2 text-[11px] font-bold text-slate-500 uppercase">{t('tierAudit.col.source')}</th>
                                            <th className="px-4 py-2 text-[11px] font-bold text-slate-500 uppercase"></th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 dark:divide-border-dark">
                                        {groupAssets.map(asset => (
                                            <tr key={asset.asset_id} className={`hover:bg-slate-50 dark:hover:bg-surface-dark ${!asset.is_rebalanceable ? 'opacity-50' : ''}`}>
                                                <td className="px-4 py-2 text-sm font-mono text-slate-700 dark:text-slate-300">{asset.asset_id}</td>
                                                <td className="px-4 py-2 text-sm text-slate-900 dark:text-slate-100">{asset.asset_name || '—'}</td>
                                                <td className="px-4 py-2 text-sm text-slate-500">{asset.class_name ? localizedClassName(asset.class_name, asset.class_name_cn, lang) : '—'}</td>
                                                <td className="px-4 py-2 text-sm font-mono text-right text-slate-700 dark:text-slate-300">
                                                    {asset.market_value_cny != null ? formatCNY(asset.market_value_cny) : '—'}
                                                </td>
                                                <td className="px-4 py-2 text-xs text-slate-400">{asset.source_system || '—'}</td>
                                                <td className="px-4 py-2">
                                                    <button
                                                        onClick={() => openTierModal(asset)}
                                                        className="text-xs px-2 py-1 rounded bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-600 dark:text-slate-300 transition-colors"
                                                    >
                                                        {t('tierAudit.changeTier')}
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Tier Assignment Modal */}
            {tierModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl p-6 w-full max-w-sm">
                        <h2 className="text-base font-bold text-slate-900 dark:text-white mb-1">{t('tierAudit.assignTier')}</h2>
                        <p className="text-xs font-mono text-slate-500 mb-4">{tierModal.asset.asset_id}</p>
                        <select
                            value={tierModal.selectedTierId}
                            onChange={e => setTierModal(prev => prev ? { ...prev, selectedTierId: e.target.value } : prev)}
                            className="w-full border border-slate-200 dark:border-border-dark rounded-lg px-3 py-2 text-sm bg-white dark:bg-surface-dark text-slate-900 dark:text-white mb-4"
                        >
                            <option value="">{t('tierAudit.noTier')}</option>
                            {tiers.map(tr => (
                                <option key={tr.id} value={tr.id}>{tr.name}</option>
                            ))}
                        </select>
                        <div className="flex gap-2 justify-end">
                            <button
                                onClick={() => setTierModal(null)}
                                className="px-3 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-surface-dark"
                            >
                                {t('tierAudit.cancel')}
                            </button>
                            <button
                                onClick={handleTierSave}
                                disabled={tierModalSaving}
                                className="px-3 py-1.5 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-medium disabled:opacity-50"
                            >
                                {tierModalSaving ? t('tierAudit.savingEllipsis') : t('tierAudit.save')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
