import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SettingsAPI } from '../../src/services/api';
import type { InvestorPhilosophy } from '../../src/services/api/settings';

interface PhilosophyDraft {
  goal: string;
  horizon: string;
  risk_tolerance: string;
  core_weakness: string;
  portfolio_structure: string;
}

function fromApi(phil: InvestorPhilosophy): PhilosophyDraft {
  return {
    goal: phil.goal ?? '',
    horizon: phil.horizon ?? '',
    risk_tolerance: phil.risk_tolerance ?? '',
    core_weakness: phil.core_weakness ?? '',
    portfolio_structure: phil.portfolio_structure ?? '',
  };
}

function fieldMeta(t: (key: string) => string): Array<{
  key: keyof PhilosophyDraft;
  label: string;
  placeholder: string;
  rows: number;
}> {
  return [
    {
      key: 'goal',
      label: t('investorProfileEditor.field.goal.label'),
      placeholder: t('investorProfileEditor.field.goal.placeholder'),
      rows: 3,
    },
    {
      key: 'horizon',
      label: t('investorProfileEditor.field.horizon.label'),
      placeholder: t('investorProfileEditor.field.horizon.placeholder'),
      rows: 3,
    },
    {
      key: 'risk_tolerance',
      label: t('investorProfileEditor.field.riskTolerance.label'),
      placeholder: t('investorProfileEditor.field.riskTolerance.placeholder'),
      rows: 3,
    },
    {
      key: 'core_weakness',
      label: t('investorProfileEditor.field.coreWeakness.label'),
      placeholder: t('investorProfileEditor.field.coreWeakness.placeholder'),
      rows: 3,
    },
    {
      key: 'portfolio_structure',
      label: t('investorProfileEditor.field.portfolioStructure.label'),
      placeholder: t('investorProfileEditor.field.portfolioStructure.placeholder'),
      rows: 8,
    },
  ];
}

export const InvestorProfileEditor: React.FC = () => {
  const { t } = useTranslation('system');
  const [draft, setDraft] = useState<PhilosophyDraft | null>(null);
  const [saved, setSaved] = useState<PhilosophyDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedOk, setSavedOk] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const profile = await SettingsAPI.getProfile();
      const d = fromApi(profile.philosophy);
      setDraft(d);
      setSaved(d);
    } catch (e: any) {
      setError(e.message ?? t('investorProfileEditor.loadError'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const isDirty = draft && saved
    ? JSON.stringify(draft) !== JSON.stringify(saved)
    : false;

  const handleChange = (key: keyof PhilosophyDraft, value: string) => {
    setDraft(prev => prev ? { ...prev, [key]: value } : prev);
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    setSavedOk(false);
    try {
      const updated = await SettingsAPI.updateProfile({ philosophy: draft });
      const d = fromApi(updated.philosophy);
      setDraft(d);
      setSaved(d);
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 3000);
    } catch (e: any) {
      setSaveError(e.message ?? t('investorProfileEditor.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (saved) setDraft({ ...saved });
    setSaveError(null);
    setSavedOk(false);
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-6 text-slate-400 dark:text-slate-500 text-sm">
        <span className="material-symbols-outlined !text-[18px] animate-spin">progress_activity</span>
        {t('investorProfileEditor.loadingProfile')}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-3 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl text-sm text-red-700 dark:text-red-400">
        <span className="material-symbols-outlined !text-[18px]">error</span>
        {error}
        <button onClick={load} className="ml-auto text-xs underline">{t('investorProfileEditor.retry')}</button>
      </div>
    );
  }

  if (!draft) return null;

  return (
    <div className="mt-8">
      {/* Section header */}
      <div className="flex items-center mb-5 gap-3">
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100 m-0">
            {t('investorProfileEditor.title')}
          </h2>
          <p className="text-xs text-slate-500 mt-1 mb-0">
            {t('investorProfileEditor.subtitle')}
          </p>
        </div>
        <button
          onClick={handleReset}
          disabled={!isDirty || saving}
          className="px-4 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-card-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {t('investorProfileEditor.reset')}
        </button>
        <button
          onClick={handleSave}
          disabled={!isDirty || saving}
          className="px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {saving && (
            <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>
          )}
          {saving ? t('investorProfileEditor.savingEllipsis') : t('investorProfileEditor.saveProfile')}
        </button>
      </div>

      {saveError && (
        <div className="mb-3 text-sm text-red-500 dark:text-red-400">{t('investorProfileEditor.saveErrorPrefix', { error: saveError })}</div>
      )}
      {savedOk && (
        <div className="mb-3 text-sm text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
          <span className="material-symbols-outlined !text-[14px]">check_circle</span>
          {t('investorProfileEditor.profileSaved')}
        </div>
      )}

      {/* Fields */}
      <div className="border border-slate-200 dark:border-border-dark rounded-xl bg-white dark:bg-card-dark overflow-hidden divide-y divide-slate-100 dark:divide-border-dark">
        {fieldMeta(t).map(({ key, label, placeholder, rows }) => {
          const fieldDirty = draft[key] !== (saved?.[key] ?? '');
          return (
            <div key={key} className="px-4 py-4">
              <div className="flex items-center gap-2 mb-2">
                <label
                  htmlFor={`investor-profile-${key}`}
                  className="text-xs font-semibold text-slate-700 dark:text-slate-200"
                >
                  {label}
                </label>
                {fieldDirty && (
                  <span className="text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-2 py-0.5 rounded font-semibold">
                    {t('investorProfileEditor.modified')}
                  </span>
                )}
              </div>
              <textarea
                id={`investor-profile-${key}`}
                value={draft[key]}
                onChange={e => handleChange(key, e.target.value)}
                placeholder={placeholder}
                rows={rows}
                className="w-full text-sm bg-slate-50 dark:bg-slate-900 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-border-dark rounded p-3 resize-y leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-slate-300 dark:placeholder:text-slate-600"
                spellCheck={false}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};
