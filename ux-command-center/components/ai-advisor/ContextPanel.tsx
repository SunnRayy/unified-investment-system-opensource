import React from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { ContextConfig } from '../../src/services/api';

interface ContextPanelProps {
  config: ContextConfig;
  onChange: (config: ContextConfig) => void;
  tokenEstimates?: Record<string, { estimated_tokens: number } | number>;
}

function tierLabels(t: TFunction<'aiAdvisor'>): Record<string, string> {
  return {
    identity: t('contextPanel.tiers.identity'),
    portfolio: t('contextPanel.tiers.portfolio'),
    market: t('contextPanel.tiers.market'),
    strategy: t('contextPanel.tiers.strategy'),
    transactions: t('contextPanel.tiers.transactions'),
  };
}

function presets(t: TFunction<'aiAdvisor'>) {
  return {
    quick: {
      label: t('contextPanel.presets.quick'),
      config: {
        tiers: {
          identity: { enabled: true, detail: 'summary' as const },
          portfolio: { enabled: true, detail: 'summary' as const },
          market: { enabled: true, detail: 'summary' as const },
          strategy: { enabled: false, detail: 'summary' as const, timeframe: '30d' },
          transactions: { enabled: true, detail: 'summary' as const, timeframe: '14d' },
        },
        include_realtime: false,
      },
    },
    deep: {
      label: t('contextPanel.presets.deep'),
      config: {
        tiers: {
          identity: { enabled: true, detail: 'detailed' as const },
          portfolio: { enabled: true, detail: 'detailed' as const },
          market: { enabled: true, detail: 'detailed' as const },
          strategy: { enabled: true, detail: 'detailed' as const, timeframe: '30d' },
          transactions: { enabled: true, detail: 'detailed' as const, timeframe: '30d' },
        },
        include_realtime: false,
      },
    },
    strategy: {
      label: t('contextPanel.presets.strategy'),
      config: {
        tiers: {
          identity: { enabled: true, detail: 'full' as const },
          portfolio: { enabled: true, detail: 'detailed' as const },
          market: { enabled: true, detail: 'summary' as const },
          strategy: { enabled: true, detail: 'full' as const, timeframe: '90d' },
          transactions: { enabled: true, detail: 'full' as const, timeframe: 'all' },
        },
        include_realtime: false,
      },
    },
  };
}

export function ContextPanel({ config, onChange, tokenEstimates }: ContextPanelProps) {
  const { t } = useTranslation('aiAdvisor');
  const TIER_LABELS = tierLabels(t);
  const PRESETS = presets(t);
  const totalTokens = typeof tokenEstimates?.total === 'number' ? tokenEstimates.total : 0;

  const applyPreset = (preset: keyof typeof PRESETS) => {
    onChange({ ...config, ...PRESETS[preset].config });
  };

  const updateTier = (tier: string, updates: Partial<{ enabled: boolean; detail: string; timeframe: string }>) => {
    onChange({
      ...config,
      tiers: {
        ...config.tiers,
        [tier]: { ...config.tiers[tier as keyof typeof config.tiers], ...updates },
      },
    });
  };

  return (
    <div className="space-y-4">
      {/* Presets */}
      <div>
        <p className="text-xs text-slate-500 mb-2">{t('contextPanel.presetsLabel')}</p>
        <div className="flex gap-2 flex-wrap">
          {Object.entries(PRESETS).map(([key, preset]) => (
            <button
              key={key}
              type="button"
              onClick={() => applyPreset(key as keyof typeof PRESETS)}
              className="px-2.5 py-1 text-xs rounded-md border border-slate-200 dark:border-border-dark hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      {/* Token Budget */}
      <div>
        <div className="flex justify-between items-center mb-1">
          <p className="text-xs text-slate-500">{t('contextPanel.estimatedTokens')}</p>
          <span className={`text-xs font-mono font-semibold ${totalTokens > 10000 ? 'text-amber-500' : 'text-emerald-500'}`}>
            {t('contextPanel.tokensApprox', { count: totalTokens.toLocaleString() })}
          </span>
        </div>
        <div className="h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${totalTokens > 10000 ? 'bg-amber-400' : 'bg-emerald-400'}`}
            style={{ width: `${Math.min(100, (totalTokens / 15000) * 100)}%` }}
          />
        </div>
      </div>

      {/* Tier controls */}
      <div className="space-y-2">
        {(Object.keys(TIER_LABELS) as Array<keyof typeof config.tiers>).map((tier) => {
          const tierConfig = config.tiers[tier];
          return (
            <div key={tier} className="flex items-center gap-2">
              <input
                type="checkbox"
                id={`tier-${tier}`}
                checked={tierConfig.enabled}
                onChange={(e) => updateTier(tier, { enabled: e.target.checked })}
                className="rounded"
              />
              <label htmlFor={`tier-${tier}`} className="text-xs text-slate-700 dark:text-slate-300 flex-1 min-w-0">
                {TIER_LABELS[tier]}
              </label>
              {/* Stacked (not side-by-side) so a tier with both a detail select and a
                  timeframe select — currently only "transactions" — never has to fit
                  two selects on one line; single-select tiers render an unchanged
                  one-item column. */}
              <div className="flex flex-col items-end gap-1 shrink-0">
                {tier !== 'strategy' && (
                  <select
                    value={tierConfig.detail}
                    onChange={(e) => updateTier(tier, { detail: e.target.value })}
                    disabled={!tierConfig.enabled}
                    className="text-xs border border-slate-200 dark:border-border-dark rounded px-1.5 py-0.5 bg-white dark:bg-card-dark disabled:opacity-40 min-w-0"
                  >
                    <option value="summary">{t('contextPanel.detail.summary')}</option>
                    <option value="detailed">{t('contextPanel.detail.detailed')}</option>
                    <option value="full">{t('contextPanel.detail.full')}</option>
                  </select>
                )}
                {(tier === 'strategy' || tier === 'transactions') && (
                  <select
                    value={(tierConfig as { timeframe?: string }).timeframe ?? (tier === 'strategy' ? '30d' : '14d')}
                    onChange={(e) => updateTier(tier, { timeframe: e.target.value })}
                    disabled={!tierConfig.enabled}
                    className="text-xs border border-slate-200 dark:border-border-dark rounded px-1.5 py-0.5 bg-white dark:bg-card-dark disabled:opacity-40 min-w-0"
                  >
                    {tier === 'strategy' ? (
                      <>
                        <option value="30d">{t('contextPanel.timeframe.last30d')}</option>
                        <option value="60d">{t('contextPanel.timeframe.last60d')}</option>
                        <option value="90d">{t('contextPanel.timeframe.last90d')}</option>
                      </>
                    ) : (
                      <>
                        <option value="14d">{t('contextPanel.timeframe.days14')}</option>
                        <option value="30d">{t('contextPanel.timeframe.days30')}</option>
                        <option value="6m">{t('contextPanel.timeframe.months6')}</option>
                        <option value="1y">{t('contextPanel.timeframe.year1')}</option>
                        <option value="all">{t('contextPanel.timeframe.all')}</option>
                      </>
                    )}
                  </select>
                )}
              </div>

            </div>
          );
        })}
      </div>

      {/* Realtime toggle */}
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="realtime"
          checked={config.include_realtime}
          onChange={(e) => onChange({ ...config, include_realtime: e.target.checked })}
          className="rounded"
        />
        <label htmlFor="realtime" className="text-xs text-slate-700 dark:text-slate-300">
          {t('contextPanel.realtimeLabel')} <span className="text-slate-400">{t('contextPanel.realtimeLatency')}</span>
        </label>
      </div>
    </div>
  );
}
