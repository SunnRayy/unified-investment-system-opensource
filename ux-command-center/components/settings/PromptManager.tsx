import React, { useCallback, useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import {
  PromptsData,
  PromptUpdatePayload,
  SettingsAPI,
} from '../../src/services/api';
import { PromptEditor } from './PromptEditor';
import { PromptPreviewModal } from './PromptPreviewModal';
import { InvestorProfileEditor } from './InvestorProfileEditor';

// Normalize CRLF→LF for consistent dirty-checking
function normalizeLF(s: string): string {
  return s.replace(/\r\n/g, '\n');
}

interface DraftState {
  shared_persona: string;
  brief_instructions: string;
  review_instructions: string;
  review_questions: string;
}

type DraftKey = keyof DraftState;

function toDraft(data: PromptsData): DraftState {
  return {
    shared_persona: data.shared_persona.text,
    brief_instructions: data.brief_instructions.text,
    review_instructions: data.review_instructions.text,
    review_questions: data.review_questions.text,
  };
}

function isDirty(draft: DraftState, saved: PromptsData): boolean {
  return (
    normalizeLF(draft.shared_persona) !== normalizeLF(saved.shared_persona.text) ||
    normalizeLF(draft.brief_instructions) !== normalizeLF(saved.brief_instructions.text) ||
    normalizeLF(draft.review_instructions) !== normalizeLF(saved.review_instructions.text) ||
    normalizeLF(draft.review_questions) !== normalizeLF(saved.review_questions.text)
  );
}

function getBlockText(data: PromptsData, key: DraftKey): string {
  return data[key].text;
}

interface PreviewState {
  promptType: 'brief' | 'review' | 'review_questions';
  sharedPersonaDraft: string | null;
  instructionsDraft: string | null;
  directCurrent?: string;
  directProposed?: string;
  directTitle?: string;
}

// Cache defaults from first load so Reset to Default works offline
let _cachedDefaults: DraftState | null = null;

export const PromptManager: React.FC<{
  onDirtyChange?: (dirty: boolean) => void;
}> = ({ onDirtyChange }) => {
  const { t } = useTranslation('system');
  const [saved, setSaved] = useState<PromptsData | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [defaults, setDefaults] = useState<DraftState | null>(_cachedDefaults);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedOk, setSavedOk] = useState(false);
  const [preview, setPreview] = useState<PreviewState | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await SettingsAPI.getPrompts();
      setSaved(data);
      const d = toDraft(data);
      setDraft(d);
      if (data.using_defaults && !_cachedDefaults) {
        _cachedDefaults = d;
        setDefaults(d);
      } else if (!_cachedDefaults) {
        _cachedDefaults = d;
        setDefaults(d);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const dirty = saved && draft ? isDirty(draft, saved) : false;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload: PromptUpdatePayload = {
        shared_persona: draft.shared_persona,
        brief_instructions: draft.brief_instructions,
        review_instructions: draft.review_instructions,
        review_questions: draft.review_questions,
      };
      const updated = await SettingsAPI.updatePrompts(payload);
      setSaved(updated);
      setDraft(toDraft(updated));
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 3000);
    } catch (e: any) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (saved) setDraft(toDraft(saved));
  };

  const handleBlockChange = (key: DraftKey, text: string) => {
    setDraft(d => d ? { ...d, [key]: text } : d);
  };

  const handleBlockReset = async (key: DraftKey) => {
    try {
      const updated = await SettingsAPI.resetPrompts([key]);
      setSaved(updated);
      setDraft(d => d ? { ...d, [key]: getBlockText(updated, key) } : d);
    } catch (e: any) {
      setSaveError(e.message);
    }
  };

  const handlePreview = (
    promptType: 'brief' | 'review' | 'review_questions',
    key: DraftKey,
  ) => {
    if (!draft) return;
    setPreview({
      promptType,
      sharedPersonaDraft: (promptType === 'brief' || promptType === 'review') ? draft.shared_persona : null,
      instructionsDraft: draft[key],
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-slate-400 dark:text-slate-500 text-sm">
        <span className="material-symbols-outlined !text-[18px] animate-spin">progress_activity</span>
        {t('promptManager.loadingPrompts')}
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-center gap-3 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl text-sm text-red-700 dark:text-red-400">
        <span className="material-symbols-outlined !text-[18px]">error</span>
        {error}
      </div>
    );
  }
  if (!saved || !draft) return null;

  const sharedDirty = normalizeLF(draft.shared_persona) !== normalizeLF(saved.shared_persona.text);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center mb-5 gap-3">
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100 m-0">
            {t('promptManager.title')}
          </h2>
          <p className="text-xs text-slate-500 mt-1 mb-0">
            {t('promptManager.subtitle')}
          </p>
        </div>
        <button
          onClick={handleReset}
          disabled={!dirty || saving}
          className="px-4 py-2 text-sm rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-card-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {t('promptManager.resetAll')}
        </button>
        <button
          onClick={handleSave}
          disabled={!dirty || saving}
          className="px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {saving && <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>}
          {saving ? t('promptManager.savingEllipsis') : t('promptManager.savePrompts')}
        </button>
      </div>

      {/* Shared persona change warning */}
      {sharedDirty && (
        <div className="mb-4 px-4 py-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg text-xs text-amber-700 dark:text-amber-400">
          ⚠ <Trans
            t={t}
            i18nKey="promptManager.sharedPersonaWarning"
            components={{ strong1: <strong />, strong2: <strong /> }}
          />
        </div>
      )}

      {saved.using_defaults && (
        <div className="mb-4 px-4 py-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg text-xs text-blue-600 dark:text-blue-400">
          {t('promptManager.usingDefaults')}
        </div>
      )}

      {saveError && (
        <div className="mb-3 text-sm text-red-500 dark:text-red-400">{t('promptManager.saveErrorPrefix', { error: saveError })}</div>
      )}
      {savedOk && (
        <div className="mb-3 text-sm text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
          <span className="material-symbols-outlined !text-[14px]">check_circle</span>
          {t('promptManager.promptsSaved')}
        </div>
      )}

      {/* Editors */}
      <PromptEditor
        label={t('promptManager.label.sharedPersona')}
        blockKey="shared_persona"
        promptType="brief"
        block={saved.shared_persona}
        draftText={draft.shared_persona}
        defaultText={defaults?.shared_persona ?? saved.shared_persona.text}
        sharedPersonaDraft={draft.shared_persona}
        onChange={text => handleBlockChange('shared_persona', text)}
        onReset={() => handleBlockReset('shared_persona')}
        onPreview={() => setPreview({
          promptType: 'brief',
          sharedPersonaDraft: null,
          instructionsDraft: null,
          directCurrent: saved.shared_persona.text,
          directProposed: draft.shared_persona,
          directTitle: 'shared_persona',
        })}
        isDirty={normalizeLF(draft.shared_persona) !== normalizeLF(saved.shared_persona.text)}
      />
      <PromptEditor
        label={t('promptManager.label.briefInstructions')}
        blockKey="brief_instructions"
        promptType="brief"
        block={saved.brief_instructions}
        draftText={draft.brief_instructions}
        defaultText={defaults?.brief_instructions ?? saved.brief_instructions.text}
        sharedPersonaDraft={draft.shared_persona}
        onChange={text => handleBlockChange('brief_instructions', text)}
        onReset={() => handleBlockReset('brief_instructions')}
        onPreview={() => handlePreview('brief', 'brief_instructions')}
        isDirty={normalizeLF(draft.brief_instructions) !== normalizeLF(saved.brief_instructions.text)}
      />
      <PromptEditor
        label={t('promptManager.label.reviewInstructions')}
        blockKey="review_instructions"
        promptType="review"
        block={saved.review_instructions}
        draftText={draft.review_instructions}
        defaultText={defaults?.review_instructions ?? saved.review_instructions.text}
        sharedPersonaDraft={draft.shared_persona}
        onChange={text => handleBlockChange('review_instructions', text)}
        onReset={() => handleBlockReset('review_instructions')}
        onPreview={() => handlePreview('review', 'review_instructions')}
        isDirty={normalizeLF(draft.review_instructions) !== normalizeLF(saved.review_instructions.text)}
      />
      <PromptEditor
        label={t('promptManager.label.reviewQuestionsPersona')}
        blockKey="review_questions"
        promptType="review_questions"
        block={saved.review_questions}
        draftText={draft.review_questions}
        defaultText={defaults?.review_questions ?? saved.review_questions.text}
        onChange={text => handleBlockChange('review_questions', text)}
        onReset={() => handleBlockReset('review_questions')}
        onPreview={() => handlePreview('review_questions', 'review_questions')}
        isDirty={normalizeLF(draft.review_questions) !== normalizeLF(saved.review_questions.text)}
      />

      {/* Preview modal */}
      {preview && (
        <PromptPreviewModal
          promptType={preview.promptType}
          sharedPersonaDraft={preview.sharedPersonaDraft}
          instructionsDraft={preview.instructionsDraft}
          directCurrent={preview.directCurrent}
          directProposed={preview.directProposed}
          directTitle={preview.directTitle}
          onClose={() => setPreview(null)}
        />
      )}

      {/* Investor Profile editor — rendered below prompts on the same ai-prompts view */}
      <InvestorProfileEditor />
    </div>
  );
};
