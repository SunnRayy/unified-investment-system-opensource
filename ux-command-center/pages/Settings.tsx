import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import {
  FullLLMSettings,
  FullLLMSettingsUpdate,
  LLMChannelUpdate,
  SettingsAPI,
} from '../src/services/api';
import ChannelCard from '../components/settings/ChannelCard';
import RuntimeParams from '../components/settings/RuntimeParams';
import { PromptManager } from '../components/settings/PromptManager';
import { DataSourceManager } from '../components/settings/DataSourceManager';
import { GeneralSettings } from '../components/settings/GeneralSettings';

function toDraft(s: FullLLMSettings): FullLLMSettingsUpdate {
  return {
    channels: s.channels.map(ch => ({
      name: ch.name,
      provider: ch.provider,
      enabled: ch.enabled,
      api_key_env: ch.api_key_env,
      api_key_value: null,
      models: [...ch.models],
    })),
    primary_model: s.primary_model,
    fallback_models: [...s.fallback_models],
    temperature: s.temperature,
    max_output_tokens: s.max_output_tokens,
  };
}

function isDraftDirty(draft: FullLLMSettingsUpdate, saved: FullLLMSettings): boolean {
  const cleanDraft = {
    ...draft,
    channels: draft.channels.map(({ api_key_value: _, ...rest }) => rest),
  };
  const cleanSaved = {
    channels: saved.channels.map(({ key_status: _, ...rest }) => rest),
    primary_model: saved.primary_model,
    fallback_models: saved.fallback_models,
    temperature: saved.temperature,
    max_output_tokens: saved.max_output_tokens,
  };
  return JSON.stringify(cleanDraft) !== JSON.stringify(cleanSaved);
}

const PAGE_META_KEYS: Record<string, { titleKey: string; subtitleKey: string }> = {
  'ai-models':   { titleKey: 'common:nav.aiModels',    subtitleKey: 'settingsPage.subtitle.aiModels' },
  'ai-prompts':  { titleKey: 'common:nav.aiPrompts',   subtitleKey: 'settingsPage.subtitle.aiPrompts' },
  'data-sources':{ titleKey: 'common:nav.dataSources', subtitleKey: 'settingsPage.subtitle.dataSources' },
  'settings':    { titleKey: 'common:nav.settings',    subtitleKey: 'settingsPage.subtitle.settings' },
};

export const Settings: React.FC = () => {
  const { t } = useTranslation(['system', 'common']);
  const location = useLocation();
  // Derive active tab from current path
  const pathKey = location.pathname.replace('/', '') || 'settings';
  const category = ['ai-models', 'ai-prompts', 'data-sources'].includes(pathKey) ? pathKey : 'settings';
  const metaKeys = PAGE_META_KEYS[category];
  const meta = { title: t(metaKeys.titleKey), subtitle: t(metaKeys.subtitleKey) };

  const [settings, setSettings] = useState<FullLLMSettings | null>(null);
  const [draft, setDraft] = useState<FullLLMSettingsUpdate | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedOk, setSavedOk] = useState(false);
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (category !== 'ai-models') return;
    setLoading(true);
    setError(null);
    try {
      const data = await SettingsAPI.getLLMSettings();
      setSettings(data);
      setDraft(toDraft(data));
    } catch (e) {
      setError(t('settingsPage.loadError'));
    } finally {
      setLoading(false);
    }
  }, [category, t]);

  useEffect(() => { load(); }, [load]);

  const isDirty = settings && draft ? isDraftDirty(draft, settings) : false;

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    setSavedOk(false);
    try {
      const updated = await SettingsAPI.updateLLMSettings(draft);
      setSettings(updated);
      setDraft(toDraft(updated));
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 3000);
    } catch (e: any) {
      setSaveError(e?.message ?? t('settingsPage.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (settings) setDraft(toDraft(settings));
    setSaveError(null);
    setSavedOk(false);
  };

  const handleChannelChange = (index: number, updated: LLMChannelUpdate) => {
    setDraft(prev => {
      if (!prev) return prev;
      const channels = [...prev.channels];
      channels[index] = updated;
      return { ...prev, channels };
    });
  };

  const handleChannelDelete = (index: number) => {
    setDraft(prev => {
      if (!prev) return prev;
      return { ...prev, channels: prev.channels.filter((_, i) => i !== index) };
    });
    setExpandedIndex(null);
  };

  const handleAddChannel = () => {
    setDraft(prev => {
      if (!prev) return prev;
      const newChannel: LLMChannelUpdate = {
        name: '', provider: 'gemini', enabled: true,
        api_key_env: 'GEMINI_API_KEY', api_key_value: null, models: [],
      };
      return { ...prev, channels: [...prev.channels, newChannel] };
    });
    setExpandedIndex(draft?.channels.length ?? 0);
  };

  const handleRuntimeChange = (updates: Partial<FullLLMSettingsUpdate>) => {
    setDraft(prev => prev ? { ...prev, ...updates } : prev);
  };

  const availableModels = draft?.channels
    .filter(c => c.enabled)
    .flatMap(c => c.models.map(m => ({ value: `${c.provider}/${m}`, label: `${c.name} / ${m}` }))) ?? [];

  return (
    <div className="flex flex-col h-full bg-background-light dark:bg-background-dark">
      {/* Header */}
      <div className="flex items-center justify-between px-8 py-5 border-b border-slate-200 dark:border-border-dark bg-white dark:bg-sidebar-dark">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">{meta.title}</h1>
          <p className="text-xs text-slate-500 mt-0.5">{meta.subtitle}</p>
        </div>
        {category === 'ai-models' && (
          <div className="flex items-center gap-3">
            {saveError && <span className="text-xs text-red-500">{saveError}</span>}
            {savedOk && (
              <span className="text-xs text-emerald-500 flex items-center gap-1">
                <span className="material-symbols-outlined !text-[14px]">check_circle</span>
                {t('settingsPage.saved')}
              </span>
            )}
            <button
              onClick={handleReset}
              disabled={!isDirty || saving}
              className="px-4 py-2 text-sm font-medium rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-card-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {t('settingsPage.reset')}
            </button>
            <button
              onClick={handleSave}
              disabled={!isDirty || saving}
              className="px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              {saving && <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>}
              {t('settingsPage.save')}
            </button>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {category === 'ai-prompts' && <PromptManager />}
        {category === 'data-sources' && <DataSourceManager />}
        {category === 'ai-models' && (
          <>
            {loading && (
              <div className="flex items-center gap-2 text-slate-500 text-sm">
                <span className="material-symbols-outlined !text-[18px] animate-spin">progress_activity</span>
                {t('settingsPage.loadingSettings')}
              </div>
            )}
            {error && (
              <div className="flex items-center gap-3 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl text-sm text-red-700 dark:text-red-400">
                <span className="material-symbols-outlined !text-[18px]">error</span>
                {error}
                <button onClick={load} className="ml-auto text-xs underline">{t('settingsPage.retry')}</button>
              </div>
            )}
            {!loading && !error && draft && settings && (
              <>
                <div className="mb-8">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('settingsPage.llmChannels')}</h2>
                      <p className="text-xs text-slate-500 mt-0.5">{t('settingsPage.llmChannelsSubtitle')}</p>
                    </div>
                    <button
                      onClick={handleAddChannel}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-primary text-primary hover:bg-primary/5 transition-colors"
                    >
                      <span className="material-symbols-outlined !text-[14px]">add</span>
                      {t('settingsPage.addChannel')}
                    </button>
                  </div>
                  <div className="space-y-3">
                    {draft.channels.map((ch, i) => {
                      const savedChannel = settings.channels[i];
                      return (
                        <ChannelCard
                          key={i}
                          channel={ch}
                          index={i}
                          keyStatus={savedChannel?.key_status ?? 'missing'}
                          isExpanded={expandedIndex === i}
                          onToggleExpand={() => setExpandedIndex(expandedIndex === i ? null : i)}
                          onChange={updated => handleChannelChange(i, updated)}
                          onDelete={() => handleChannelDelete(i)}
                          onTest={SettingsAPI.testChannel}
                        />
                      );
                    })}
                    {draft.channels.length === 0 && (
                      <div className="text-center py-8 text-slate-400 text-sm border border-dashed border-slate-200 dark:border-border-dark rounded-xl">
                        {t('settingsPage.noChannels')}
                      </div>
                    )}
                  </div>
                </div>
                <RuntimeParams
                  settings={draft}
                  availableModels={availableModels}
                  onChange={handleRuntimeChange}
                />
              </>
            )}
          </>
        )}
        {category === 'settings' && <GeneralSettings />}
      </div>
    </div>
  );
};

export default Settings;
