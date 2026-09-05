import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FullLLMSettingsUpdate, SettingsAPI } from '../../src/services/api';
import type { LLMUsageResponse } from '../../src/services/api/types';

interface RuntimeParamsProps {
  settings: FullLLMSettingsUpdate;
  availableModels: { value: string; label: string }[];
  onChange: (updates: Partial<FullLLMSettingsUpdate>) => void;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatLastUsed(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
}

const RuntimeParams: React.FC<RuntimeParamsProps> = ({ settings, availableModels, onChange }) => {
  const { t } = useTranslation('system');
  const [usage, setUsage] = useState<LLMUsageResponse | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState<string | null>(null);

  const loadUsage = useCallback(async () => {
    setUsageLoading(true);
    setUsageError(null);
    try {
      const data = await SettingsAPI.getLLMUsage();
      setUsage(data);
    } catch (err) {
      setUsageError(err instanceof Error ? err.message : t('runtimeParams.loadUsageError'));
    } finally {
      setUsageLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadUsage();
  }, [loadUsage]);
  const fallbackOptions = availableModels.filter(m => m.value !== settings.primary_model);

  const toggleFallback = (modelValue: string) => {
    const current = settings.fallback_models;
    const next = current.includes(modelValue)
      ? current.filter(m => m !== modelValue)
      : [...current, modelValue];
    onChange({ fallback_models: next });
  };

  const moveUp = (index: number) => {
    if (index <= 0) return;
    const next = [...settings.fallback_models];
    [next[index - 1], next[index]] = [next[index], next[index - 1]];
    onChange({ fallback_models: next });
  };

  const moveDown = (index: number) => {
    if (index >= settings.fallback_models.length - 1) return;
    const next = [...settings.fallback_models];
    [next[index], next[index + 1]] = [next[index + 1], next[index]];
    onChange({ fallback_models: next });
  };

  const selectedFallbacks = settings.fallback_models.filter(v =>
    fallbackOptions.some(m => m.value === v)
  );
  const availableToAdd = fallbackOptions.filter(m => !settings.fallback_models.includes(m.value));

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('runtimeParams.title')}</h2>
        <p className="text-xs text-slate-500 mt-0.5">{t('runtimeParams.subtitle')}</p>
      </div>


      <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl divide-y divide-slate-100 dark:divide-border-dark">
        {/* Primary model */}
        <div className="px-5 py-4">
          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-2">{t('runtimeParams.primaryModel')}</label>
          {availableModels.length === 0 ? (
            <p className="text-xs text-slate-400 italic">{t('runtimeParams.enableChannelHint')}</p>
          ) : (
            <select
              value={settings.primary_model}
              onChange={e => onChange({ primary_model: e.target.value })}
              className="w-full max-w-sm px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
            >
              {availableModels.map(m => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          )}
        </div>

        {/* Fallback models */}
        <div className="px-5 py-4">
          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-2">
            {t('runtimeParams.fallbackModels')} <span className="font-normal text-slate-400">{t('runtimeParams.fallbackModelsHint')}</span>
          </label>
          {fallbackOptions.length === 0 ? (
            <p className="text-xs text-slate-400 italic">{t('runtimeParams.addMoreModelsHint')}</p>
          ) : (
            <div className="space-y-3">
              {/* Priority order list */}
              {selectedFallbacks.length > 0 && (
                <div className="space-y-1">
                  <span className="text-[10px] text-slate-400 uppercase tracking-wider">{t('runtimeParams.priorityOrder')}</span>
                  {selectedFallbacks.map((modelValue, idx) => {
                    const label = fallbackOptions.find(m => m.value === modelValue)?.label ?? modelValue;
                    return (
                      <div
                        key={modelValue}
                        className="flex items-center gap-2 py-1.5 px-2 bg-slate-50 dark:bg-surface-dark border border-slate-100 dark:border-border-dark rounded-lg"
                      >
                        <span className="text-[10px] text-slate-400 w-4 text-right tabular-nums">{idx + 1}.</span>
                        <span className="text-xs font-mono text-slate-700 dark:text-slate-300 flex-1 truncate">{label}</span>
                        <button
                          onClick={() => moveUp(settings.fallback_models.indexOf(modelValue))}
                          disabled={idx === 0}
                          className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-25 disabled:cursor-not-allowed transition-colors px-1"
                          title={t('runtimeParams.moveUp')}
                        >
                          ↑
                        </button>
                        <button
                          onClick={() => moveDown(settings.fallback_models.indexOf(modelValue))}
                          disabled={idx === selectedFallbacks.length - 1}
                          className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-25 disabled:cursor-not-allowed transition-colors px-1"
                          title={t('runtimeParams.moveDown')}
                        >
                          ↓
                        </button>
                        <button
                          onClick={() => toggleFallback(modelValue)}
                          className="text-slate-300 hover:text-red-400 dark:hover:text-red-400 transition-colors px-1"
                          title={t('runtimeParams.remove')}
                        >
                          ×
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Available to add */}
              {availableToAdd.length > 0 && (
                <div className="space-y-1.5">
                  {selectedFallbacks.length > 0 && (
                    <span className="text-[10px] text-slate-400 uppercase tracking-wider">{t('runtimeParams.addMore')}</span>
                  )}
                  {availableToAdd.map(m => (
                    <label key={m.value} className="flex items-center gap-2.5 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={false}
                        onChange={() => toggleFallback(m.value)}
                        className="rounded border-slate-300 dark:border-slate-600 text-primary focus:ring-primary/30"
                      />
                      <span className="text-xs font-mono text-slate-700 dark:text-slate-300 group-hover:text-slate-900 dark:group-hover:text-white transition-colors">
                        {m.label}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Temperature */}
        <div className="px-5 py-4">
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-slate-600 dark:text-slate-400">{t('runtimeParams.temperature')}</label>
            <span className="text-sm font-bold text-primary tabular-nums">{settings.temperature.toFixed(1)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={settings.temperature}
            onChange={e => onChange({ temperature: parseFloat(e.target.value) })}
            className="w-full max-w-sm accent-primary"
          />
          <div className="flex justify-between text-[10px] text-slate-400 max-w-sm mt-1">
            <span>{t('runtimeParams.tempPrecise')}</span>
            <span>{t('runtimeParams.tempBalanced')}</span>
            <span>{t('runtimeParams.tempCreative')}</span>
          </div>
        </div>

        {/* Max output tokens */}
        <div className="px-5 py-4">
          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-2">{t('runtimeParams.maxOutputTokens')}</label>
          <input
            type="number"
            min={256}
            max={32768}
            step={256}
            value={settings.max_output_tokens}
            onChange={e => onChange({ max_output_tokens: parseInt(e.target.value, 10) || 4096 })}
            className="w-40 px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30 tabular-nums"
          />
          <p className="text-[10px] text-slate-400 mt-1">{t('runtimeParams.tokenRange')}</p>
        </div>
      </div>

      {/* Usage & Cost card */}
      <div className="mt-6">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('runtimeParams.usageCost')}</h2>
            <p className="text-xs text-slate-500 mt-0.5">{t('runtimeParams.usageCostSubtitle')}</p>
          </div>
          <button
            onClick={() => { void loadUsage(); }}
            disabled={usageLoading}
            className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-surface-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {usageLoading ? t('runtimeParams.loadingEllipsis') : t('runtimeParams.refresh')}
          </button>
        </div>

        {usageError ? (
          <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-700 dark:text-red-400">
            {usageError}
          </div>
        ) : usageLoading && !usage ? (
          <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark px-5 py-6 text-xs text-slate-400 text-center">
            {t('runtimeParams.loadingEllipsis')}
          </div>
        ) : usage && usage.models.length === 0 ? (
          <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark px-5 py-6 text-xs text-slate-400 text-center italic">
            {t('runtimeParams.noUsageRecorded')}
          </div>
        ) : usage ? (
          <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-100 dark:border-border-dark bg-slate-50 dark:bg-surface-dark">
                  <th className="px-4 py-2.5 text-left font-semibold text-slate-500 dark:text-slate-400">{t('runtimeParams.col.model')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-slate-500 dark:text-slate-400">{t('runtimeParams.col.calls')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-slate-500 dark:text-slate-400">{t('runtimeParams.col.tokens')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-slate-500 dark:text-slate-400">{t('runtimeParams.col.costUsd')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-slate-500 dark:text-slate-400">{t('runtimeParams.col.lastUsed')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-border-dark">
                {usage.models.map(row => (
                  <tr key={row.model_used} className="hover:bg-slate-50 dark:hover:bg-surface-dark transition-colors">
                    <td className="px-4 py-2.5 font-mono text-slate-700 dark:text-slate-300 truncate max-w-[200px]">
                      {row.model_used}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                      {row.calls.toLocaleString()}
                      {row.failure_calls > 0 && (
                        <span className="ml-1 text-red-400">{t('runtimeParams.errCount', { count: row.failure_calls })}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                      {formatTokens(row.total_tokens)}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                      ${row.cost_usd.toFixed(4)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-slate-500 dark:text-slate-400">
                      {formatLastUsed(row.last_used)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-surface-dark font-semibold">
                  <td className="px-4 py-2.5 text-slate-600 dark:text-slate-400">{t('runtimeParams.total')}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                    {usage.total_calls.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                    {formatTokens(usage.total_tokens)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                    ${usage.total_cost_usd.toFixed(4)}
                  </td>
                  <td className="px-4 py-2.5" />
                </tr>
              </tfoot>
            </table>
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default RuntimeParams;
