/**
 * Huinsight i18n bootstrap (Program BIL / WS-0, ADR-028).
 *
 * English is the DEFAULT locale and every EN catalog value is byte-identical to the
 * literal it replaced. That is deliberate: the existing vitest suite asserts on English
 * literals, so it doubles as the extraction's correctness gate. Paraphrase an EN value
 * and a test goes red — fix the catalog, not the test.
 *
 * Resources are bundled statically (plain JSON imports, no HTTP backend). Huinsight is a
 * self-hosted single-user dashboard; the catalogs are small and an extra network hop
 * would only add a flash-of-untranslated-content on a cold load.
 *
 * Importing this module initializes the default i18next instance synchronously, so
 * `useTranslation()` works on first render without Suspense.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import enAiAdvisor from './locales/en/aiAdvisor.json';
import enCommon from './locales/en/common.json';
import enErrors from './locales/en/errors.json';
import enIncomeExpense from './locales/en/incomeExpense.json';
import enManagement from './locales/en/management.json';
import enOperations from './locales/en/operations.json';
import enPerformance from './locales/en/performance.json';
import enPortfolio from './locales/en/portfolio.json';
import enReports from './locales/en/reports.json';
import enSystem from './locales/en/system.json';
import enValuation from './locales/en/valuation.json';

import zhAiAdvisor from './locales/zh-CN/aiAdvisor.json';
import zhCommon from './locales/zh-CN/common.json';
import zhErrors from './locales/zh-CN/errors.json';
import zhIncomeExpense from './locales/zh-CN/incomeExpense.json';
import zhManagement from './locales/zh-CN/management.json';
import zhOperations from './locales/zh-CN/operations.json';
import zhPerformance from './locales/zh-CN/performance.json';
import zhPortfolio from './locales/zh-CN/portfolio.json';
import zhReports from './locales/zh-CN/reports.json';
import zhSystem from './locales/zh-CN/system.json';
import zhValuation from './locales/zh-CN/valuation.json';

/** localStorage key. Unchanged from the pre-i18next implementation on purpose — see below. */
export const LANGUAGE_STORAGE_KEY = 'uis-lang';

export const SUPPORTED_LANGUAGES = ['en', 'zh-CN'] as const;
export type Lang = (typeof SUPPORTED_LANGUAGES)[number];

export const DEFAULT_LANGUAGE: Lang = 'en';

/** The 11 namespaces. Kept in sync with `scripts/i18n-parity-check.mjs`'s REQUIRED_NAMESPACES. */
export const NAMESPACES = [
  'common',
  'portfolio',
  'performance',
  'reports',
  'incomeExpense',
  'valuation',
  'aiAdvisor',
  'operations',
  'management',
  'system',
  'errors',
] as const;

/**
 * Legacy shim: the pre-i18next implementation stored the bare macrolanguage code `'zh'`.
 * i18next is configured with BCP-47 `supportedLngs: ['en', 'zh-CN']` and does NOT do
 * non-explicit fallback, so a stored `'zh'` would resolve to `'en'` — silently resetting a
 * user who had already chosen Chinese. Normalize the stored value once, in place, BEFORE
 * i18next reads it. Idempotent: a value that is already `'zh-CN'` is left untouched.
 */
export function normalizeLegacyStoredLanguage(): void {
  try {
    if (typeof localStorage === 'undefined') return;
    if (localStorage.getItem(LANGUAGE_STORAGE_KEY) === 'zh') {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-CN');
    }
  } catch {
    // Private browsing / storage disabled — detection falls through to `navigator`.
  }
}

/** Map anything language-ish onto a supported locale. `zh`, `zh-Hans`, `zh-SG` → `zh-CN`. */
export function normalizeLang(value: string | undefined | null): Lang {
  if (!value) return DEFAULT_LANGUAGE;
  if ((SUPPORTED_LANGUAGES as readonly string[]).includes(value)) return value as Lang;
  if (value.toLowerCase().startsWith('zh')) return 'zh-CN';
  return DEFAULT_LANGUAGE;
}

normalizeLegacyStoredLanguage();

export const resources = {
  en: {
    common: enCommon,
    portfolio: enPortfolio,
    performance: enPerformance,
    reports: enReports,
    incomeExpense: enIncomeExpense,
    valuation: enValuation,
    aiAdvisor: enAiAdvisor,
    operations: enOperations,
    management: enManagement,
    system: enSystem,
    errors: enErrors,
  },
  'zh-CN': {
    common: zhCommon,
    portfolio: zhPortfolio,
    performance: zhPerformance,
    reports: zhReports,
    incomeExpense: zhIncomeExpense,
    valuation: zhValuation,
    aiAdvisor: zhAiAdvisor,
    operations: zhOperations,
    management: zhManagement,
    system: zhSystem,
    errors: zhErrors,
  },
} as const;

if (!i18n.isInitialized) {
  i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources,
      fallbackLng: DEFAULT_LANGUAGE,
      supportedLngs: [...SUPPORTED_LANGUAGES],
      ns: [...NAMESPACES],
      defaultNS: 'common',
      // React already escapes rendered output; double-escaping mangles `&` in strings
      // like "Income & Expense".
      interpolation: { escapeValue: false },
      detection: {
        order: ['localStorage', 'navigator'],
        lookupLocalStorage: LANGUAGE_STORAGE_KEY,
        caches: ['localStorage'],
      },
      // Resources are bundled, so nothing is ever loaded async — Suspense would only
      // add a render tier for a promise that is already resolved.
      react: { useSuspense: false },
      returnNull: false,
    });
}

/**
 * `<html lang>` sync lives here, not in a React effect. It is a document-level concern
 * (CJK font selection, screen readers, `:lang()` rules) that must hold regardless of which
 * component tree is mounted — including in tests that render a single component.
 * Registered once, at module scope.
 */
function syncDocumentLang(lng: string): void {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = normalizeLang(lng);
}

i18n.on('languageChanged', syncDocumentLang);
syncDocumentLang(i18n.resolvedLanguage ?? i18n.language ?? DEFAULT_LANGUAGE);

export default i18n;
