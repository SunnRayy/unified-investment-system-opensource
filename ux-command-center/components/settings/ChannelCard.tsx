import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChannelTestResult, LLMChannelUpdate } from '../../src/services/api';

const PROVIDERS = ['gemini', 'deepseek', 'anthropic', 'openai'];

const PROVIDER_COLORS: Record<string, string> = {
  gemini: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  deepseek: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  anthropic: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  openai: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
};

interface ChannelCardProps {
  channel: LLMChannelUpdate;
  index: number;
  keyStatus: 'configured' | 'missing';
  isExpanded: boolean;
  onToggleExpand: () => void;
  onChange: (updated: LLMChannelUpdate) => void;
  onDelete: () => void;
  onTest: (req: { provider: string; model: string; api_key: string }) => Promise<ChannelTestResult>;
}

const ChannelCard: React.FC<ChannelCardProps> = ({
  channel,
  keyStatus,
  isExpanded,
  onToggleExpand,
  onChange,
  onDelete,
  onTest,
}) => {
  const { t } = useTranslation('system');
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ChannelTestResult | null>(null);

  const providerColor = PROVIDER_COLORS[channel.provider] ?? 'bg-slate-100 text-slate-600';
  const effectiveKeyStatus = channel.api_key_value ? 'configured' : keyStatus;

  const handleTest = async () => {
    const apiKey = channel.api_key_value || '';
    const model = channel.models[0] ?? '';
    if (!apiKey) {
      setTestResult({ success: false, model: `${channel.provider}/${model}`, latency_ms: null, error: t('channelCard.enterApiKeyToTest') });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await onTest({ provider: channel.provider, model, api_key: apiKey });
      setTestResult(result);
    } finally {
      setTesting(false);
    }
  };

  const handleModelsBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    const models = e.target.value
      .split(',')
      .map(m => m.trim())
      .filter(Boolean);
    onChange({ ...channel, models });
  };

  const handleProviderChange = (provider: string) => {
    const api_key_env = `${provider.toUpperCase()}_API_KEY`;
    onChange({ ...channel, provider, api_key_env });
  };

  return (
    <div className="border border-slate-200 dark:border-border-dark rounded-xl bg-white dark:bg-card-dark overflow-hidden">
      {/* Collapsed row */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Enable toggle */}
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onChange({ ...channel, enabled: !channel.enabled }); }}
          className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors focus:outline-none ${
            channel.enabled ? 'bg-primary' : 'bg-slate-300 dark:bg-slate-600'
          }`}
          aria-label={channel.enabled ? t('channelCard.disableChannel') : t('channelCard.enableChannel')}
        >
          <span className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
            channel.enabled ? 'translate-x-5' : 'translate-x-1'
          }`} />
        </button>

        {/* Name + provider */}
        <div className="flex-1 flex items-center gap-2 min-w-0 cursor-pointer" onClick={onToggleExpand}>
          <span className="text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">
            {channel.name || <span className="italic text-slate-400">{t('channelCard.unnamedChannel')}</span>}
          </span>
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider ${providerColor}`}>
            {channel.provider}
          </span>
        </div>

        {/* Model count + key status */}
        <div className="flex items-center gap-3 text-xs text-slate-500 cursor-pointer" onClick={onToggleExpand}>
          <span>{t('channelCard.modelsCount', { count: channel.models.length })}</span>
          <span className={`flex items-center gap-1 ${effectiveKeyStatus === 'configured' ? 'text-emerald-500' : 'text-red-400'}`}>
            <span className="material-symbols-outlined !text-[14px]">
              {effectiveKeyStatus === 'configured' ? 'check_circle' : 'cancel'}
            </span>
            {effectiveKeyStatus === 'configured' ? t('channelCard.keySet') : t('channelCard.noKey')}
          </span>
        </div>

        {/* Expand / delete */}
        <button
          type="button"
          onClick={onToggleExpand}
          className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors"
          aria-label={isExpanded ? t('channelCard.collapse') : t('channelCard.expand')}
        >
          <span className="material-symbols-outlined !text-[18px]">
            {isExpanded ? 'expand_less' : 'expand_more'}
          </span>
        </button>
        <button
          type="button"
          onClick={e => { e.stopPropagation(); onDelete(); }}
          className="text-slate-300 hover:text-red-400 transition-colors"
          aria-label={t('channelCard.deleteChannel')}
        >
          <span className="material-symbols-outlined !text-[18px]">close</span>
        </button>
      </div>

      {/* Expanded form */}
      {isExpanded && (
        <div className="border-t border-slate-100 dark:border-border-dark px-4 py-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            {/* Channel Name */}
            <div>
              <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">{t('channelCard.channelName')}</label>
              <input
                type="text"
                value={channel.name}
                onChange={e => onChange({ ...channel, name: e.target.value })}
                placeholder={t('channelCard.channelNamePlaceholder')}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>

            {/* Provider */}
            <div>
              <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">{t('channelCard.provider')}</label>
              <select
                value={channel.provider}
                onChange={e => handleProviderChange(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                {PROVIDERS.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>

          {/* API Key */}
          <div>
            <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
              {t('channelCard.apiKey')}
              <span className="ml-2 font-normal text-slate-400">({channel.api_key_env})</span>
            </label>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={channel.api_key_value ?? ''}
                  onChange={e => onChange({ ...channel, api_key_value: e.target.value || null })}
                  placeholder={keyStatus === 'configured' ? t('channelCard.keyAlreadySet') : t('channelCard.pasteApiKey')}
                  className={`w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono ${channel.api_key_value !== null ? 'pr-9' : 'pr-3'}`}
                />
                {channel.api_key_value !== null && (
                  <button
                    type="button"
                    onClick={() => setShowKey(v => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                    aria-label={showKey ? t('channelCard.hideKey') : t('channelCard.showKey')}
                  >
                    <span className="material-symbols-outlined !text-[16px]">{showKey ? 'visibility_off' : 'visibility'}</span>
                  </button>
                )}
              </div>
              <span className={`flex items-center gap-1 text-xs font-medium flex-shrink-0 ${
                effectiveKeyStatus === 'configured' ? 'text-emerald-500' : 'text-red-400'
              }`}>
                <span className="material-symbols-outlined !text-[14px]">
                  {effectiveKeyStatus === 'configured' ? 'check_circle' : 'cancel'}
                </span>
                {effectiveKeyStatus === 'configured' ? t('channelCard.set') : t('channelCard.missing')}
              </span>
            </div>
          </div>

          {/* Models */}
          <div>
            <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
              {t('channelCard.models')} <span className="font-normal text-slate-400">{t('channelCard.commaSeparated')}</span>
            </label>
            <input
              type="text"
              defaultValue={channel.models.join(', ')}
              onBlur={handleModelsBlur}
              placeholder={t('channelCard.modelsPlaceholder')}
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
            />
          </div>

          {/* Test connection */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleTest}
              disabled={testing}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-surface-dark disabled:opacity-50 transition-colors"
            >
              {testing
                ? <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>
                : <span className="material-symbols-outlined !text-[14px]">wifi_tethering</span>
              }
              {t('channelCard.testConnection')}
            </button>
            {testResult && (
              <span className={`text-xs font-medium flex items-center gap-1 ${
                testResult.success ? 'text-emerald-500' : 'text-red-400'
              }`}>
                <span className="material-symbols-outlined !text-[14px]">
                  {testResult.success ? 'check_circle' : 'error'}
                </span>
                {testResult.success
                  ? t('channelCard.okLatency', { ms: testResult.latency_ms })
                  : testResult.error ?? t('channelCard.failed')
                }
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default ChannelCard;
