import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { act, render, screen } from '../test-utils';
import { ThemeProvider } from '../src/theme/useTheme';
import { PortfolioFilterProvider } from '../src/context/usePortfolioFilter';
import { Layout } from '../components/Layout';
import i18n from '../src/i18n';

/**
 * zh-CN canary (Program BIL / WS-0, ADR-028).
 *
 * The whole suite is pinned to `en` in vitest.setup.ts so the ~304 existing English
 * assertions keep working as the extraction's correctness gate. The cost of that is that
 * NOTHING would exercise the Chinese catalog — it could be empty, malformed, or wired to
 * the wrong locale code and every test would still pass.
 *
 * This is the one test that flips to zh-CN. It is deliberately thin: parity and
 * completeness are `scripts/i18n-parity-check.mjs`'s job. What this proves is that the
 * runtime path works end to end — locale switch → catalog lookup → rendered Chinese —
 * and that the BCP-47 code is `zh-CN`, not the legacy `zh`.
 */
describe('zh-CN catalog canary', () => {
  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage('en');
    });
  });

  it('renders sidebar section headers and nav labels in Chinese', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh-CN');
    });

    render(
      <ThemeProvider>
        <PortfolioFilterProvider>
          <Layout>
            <div>content</div>
          </Layout>
        </PortfolioFilterProvider>
      </ThemeProvider>,
    );

    expect(screen.getByText('投资组合')).toBeInTheDocument();
    expect(screen.getByText('综合持仓')).toBeInTheDocument();
    expect(screen.getByText('资产负债表')).toBeInTheDocument();

    // English must be gone, not merely joined by Chinese.
    expect(screen.queryByText('Unified Portfolio')).not.toBeInTheDocument();
    expect(screen.queryByText('Balance Sheet')).not.toBeInTheDocument();
  });

  it('uses the BCP-47 code zh-CN and syncs <html lang>', async () => {
    await act(async () => {
      await i18n.changeLanguage('zh-CN');
    });

    expect(i18n.resolvedLanguage).toBe('zh-CN');
    expect(document.documentElement.lang).toBe('zh-CN');
  });

  it('normalizes the legacy localStorage value "zh" to zh-CN', async () => {
    const { normalizeLegacyStoredLanguage, LANGUAGE_STORAGE_KEY, normalizeLang } = await import(
      '../src/i18n'
    );

    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh');
    normalizeLegacyStoredLanguage();
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh-CN');

    // Idempotent — a second pass must not mangle the already-normalized value.
    normalizeLegacyStoredLanguage();
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh-CN');

    expect(normalizeLang('zh')).toBe('zh-CN');
    expect(normalizeLang('zh-Hans')).toBe('zh-CN');
    expect(normalizeLang('en')).toBe('en');
    expect(normalizeLang(undefined)).toBe('en');
  });

  /**
   * The two halves of "a returning Chinese user is not reset to English": the normalizer
   * writes zh-CN into the legacy key, and the detector is actually reading THAT key. The
   * detector is configured with `lookupLocalStorage: 'uis-lang'`; a typo there would leave
   * every other test green while silently resetting Ray on his next page load.
   */
  it('detects the stored language from the legacy uis-lang key', async () => {
    const { LANGUAGE_STORAGE_KEY, normalizeLegacyStoredLanguage } = await import('../src/i18n');
    const detector = (i18n.services as { languageDetector?: { detect: () => string[] | string } })
      .languageDetector;
    expect(detector).toBeDefined();

    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh');
    normalizeLegacyStoredLanguage();

    const detected = detector!.detect();
    expect(Array.isArray(detected) ? detected[0] : detected).toBe('zh-CN');
  });

  it('persists the chosen language back to uis-lang', async () => {
    const { LANGUAGE_STORAGE_KEY } = await import('../src/i18n');

    await act(async () => {
      await i18n.changeLanguage('zh-CN');
    });
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh-CN');

    await act(async () => {
      await i18n.changeLanguage('en');
    });
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
  });
});
