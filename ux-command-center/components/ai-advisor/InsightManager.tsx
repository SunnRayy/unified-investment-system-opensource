import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { InsightItem } from '../../src/services/api';
import { promoteInsight, addValidatedCase, setRuleLayer } from '../../src/services/api';
import { useLanguage } from '../../src/context/useLanguage';
import { formatDate } from '../../src/utils/formatMoney';

interface InsightManagerProps {
  insights: InsightItem[];
  onRefresh: () => void;
}

const STATUS_COLORS: Record<string, string> = {
  raw: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  recurring: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  validated: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  principle: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  deprecated: 'bg-slate-50 text-slate-400',
};

function statusLabels(t: TFunction<'aiAdvisor'>): Record<string, string> {
  return {
    raw: t('insightManager.status.raw'),
    recurring: t('insightManager.status.recurring'),
    validated: t('insightManager.status.validated'),
    principle: t('insightManager.status.principle'),
    deprecated: t('insightManager.status.deprecated'),
  };
}

function categoryLabels(t: TFunction<'aiAdvisor'>): Record<string, string> {
  return {
    risk: t('insightManager.category.risk'),
    timing: t('insightManager.category.timing'),
    sizing: t('insightManager.category.sizing'),
    strategy: t('insightManager.category.strategy'),
    process: t('insightManager.category.process'),
  };
}

// F6 (PRD 2026-07-07): required insight classification — candidate for the
// system prompt ('principle') vs. exported to the grouped checklist document
// ('checklist_item'). Null/legacy rows show as "Unset".
function ruleLayerLabels(t: TFunction<'aiAdvisor'>): Record<string, string> {
  return {
    principle: t('insightManager.status.principle'),
    checklist_item: t('insightManager.ruleLayer.checklistItem'),
  };
}

export const InsightManager: React.FC<InsightManagerProps> = ({ insights, onRefresh }) => {
  const { t } = useTranslation('aiAdvisor');
  const STATUS_LABELS = statusLabels(t);
  const CATEGORY_LABELS = categoryLabels(t);
  const RULE_LAYER_LABELS = ruleLayerLabels(t);
  const { lang } = useLanguage();
  const [filterStatus, setFilterStatus] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [promoting, setPromoting] = useState<number | null>(null);
  const [addingCase, setAddingCase] = useState<number | null>(null);
  const [settingRuleLayer, setSettingRuleLayer] = useState<number | null>(null);

  const filtered = insights.filter((item) => {
    if (filterStatus && item.status !== filterStatus) return false;
    if (filterCategory && item.category !== filterCategory) return false;
    return true;
  });

  const handlePromote = async (id: number) => {
    setPromoting(id);
    try {
      await promoteInsight(id);
      onRefresh();
    } catch {
      // silent fail
    } finally {
      setPromoting(null);
    }
  };

  // F6: manually record a validated case (link + optional note) — one of the
  // two promote-gate paths (confidence >= 70% OR validated_cases >= 3).
  const handleAddCase = async (id: number) => {
    const link = window.prompt(t('insightManager.prompts.caseLink'));
    if (!link) return;
    const note = window.prompt(t('insightManager.prompts.note')) || undefined;
    setAddingCase(id);
    try {
      await addValidatedCase(id, link, note);
      onRefresh();
    } catch {
      // silent fail — matches existing handlePromote convention
    } finally {
      setAddingCase(null);
    }
  };

  const handleSetRuleLayer = async (id: number, value: string) => {
    if (!value) return;
    setSettingRuleLayer(id);
    try {
      await setRuleLayer(id, value as 'principle' | 'checklist_item');
      onRefresh();
    } catch {
      // silent fail
    } finally {
      setSettingRuleLayer(null);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-5">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('insightManager.title')}</h3>
        <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500">
          {insights.length}
        </span>
        <div className="flex items-center gap-2 ml-auto flex-wrap">
          {/* Status filter */}
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="text-xs rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">{t('insightManager.allStatuses')}</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          {/* Category filter */}
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="text-xs rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">{t('insightManager.allCategories')}</option>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Content */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <span className="material-symbols-outlined !text-[36px] text-slate-300 dark:text-slate-600 mb-3">
            lightbulb
          </span>
          <p className="text-sm text-slate-400">
            {insights.length === 0
              ? t('insightManager.emptyNoInsights')
              : t('insightManager.emptyNoMatch')}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-slate-100 dark:border-slate-700 p-4 hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors"
            >
              {/* Title row */}
              <div className="flex items-start gap-2 mb-2 flex-wrap">
                <span className="font-medium text-sm text-slate-800 dark:text-slate-100 flex-1 min-w-0">
                  {item.title}
                </span>
                <span
                  className={`text-[10px] px-2 py-0.5 rounded-full font-medium shrink-0 ${
                    STATUS_COLORS[item.status] ?? STATUS_COLORS.raw
                  }`}
                >
                  {STATUS_LABELS[item.status] ?? item.status}
                </span>
                {item.category && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 shrink-0">
                    {CATEGORY_LABELS[item.category] ?? item.category}
                  </span>
                )}
                {/* F6: rule_layer badge/selector — required classification for governed insights */}
                <select
                  value={item.rule_layer ?? ''}
                  onChange={(e) => handleSetRuleLayer(item.id, e.target.value)}
                  disabled={settingRuleLayer === item.id}
                  title={t('insightManager.ruleLayer.selectTitle')}
                  className="text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0 border-0 bg-slate-100 dark:bg-slate-800 text-slate-500 focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
                >
                  <option value="">{t('insightManager.ruleLayer.unset')}</option>
                  {Object.entries(RULE_LAYER_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </div>

              {/* Body — only shown when non-empty and distinct from title */}
              {item.body && item.body !== item.title && (
                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
                  {item.body}
                </p>
              )}

              {/* Footer */}
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-3 text-xs text-slate-400">
                  {item.recurrence_count > 1 && (
                    <span className="flex items-center gap-1">
                      <span className="material-symbols-outlined !text-[12px]">repeat</span>
                      {t('insightManager.recurringCount', { count: item.recurrence_count })}
                    </span>
                  )}
                  <span>
                    {formatDate(item.created_at, lang)}
                  </span>
                  {item.confidence != null && (
                    <span>{t('insightManager.confidence', { pct: Math.round(item.confidence * 100) })}</span>
                  )}
                  {/* F6: validated-cases counter + add-case action (one of the two promote-gate paths) */}
                  <span className="flex items-center gap-1">
                    {t('insightManager.validatedCases', { count: item.validated_cases })}
                    <button
                      type="button"
                      onClick={() => handleAddCase(item.id)}
                      disabled={addingCase === item.id}
                      className="text-primary hover:underline disabled:opacity-50"
                    >
                      {t('insightManager.addCase')}
                    </button>
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handlePromote(item.id)}
                  disabled={item.status === 'principle' || !item.promote_eligible || promoting === item.id}
                  title={
                    item.status === 'principle'
                      ? t('insightManager.alreadyPrinciple')
                      : !item.promote_eligible
                      ? (item.promote_blocked_reason ?? t('insightManager.promotionCriteriaNotMet'))
                      : undefined
                  }
                  className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-md border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {promoting === item.id ? (
                    <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                  ) : (
                    <span className="material-symbols-outlined !text-[12px]">arrow_upward</span>
                  )}
                  {t('insightManager.promote')}
                </button>
              </div>
              {/* F6: unmet-criterion hint, visible below the gate (not just on hover) */}
              {item.status !== 'principle' && !item.promote_eligible && item.promote_blocked_reason && (
                <p className="mt-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                  {t('insightManager.promoteBlocked', { reason: item.promote_blocked_reason })}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
