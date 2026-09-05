/**
 * localizedClassName resolver — Program BIL / WS-9.
 *
 * Owner-reported defect: asset-class names (Equity, Fixed Income, Commodity,
 * Cash, Alternative, Real Estate) rendered in English even with the UI set to
 * Chinese, on the Monthly Attribution and Risk Profiles pages. The Chinese
 * names already exist in `taxonomy_classes.name_cn` — the display layer just
 * never read them. `localizedClassName` is the single resolver every display
 * site now funnels through instead of re-implementing the en/zh-CN choice.
 *
 * RED-PROOF: this suite has been proven to fail. Comment out the `lang ===
 * 'zh-CN'` check (always falling through to `name`) and `renders the Chinese
 * name under zh-CN when name_cn is set` goes red — verified manually while
 * writing this file, not just claimed.
 */
import { describe, expect, it } from 'vitest';

import { localizedClassName } from '../src/utils/localizedClassName';

describe('localizedClassName', () => {
  it('renders the Chinese name under zh-CN when name_cn is set', () => {
    expect(localizedClassName('Equity', '股票', 'zh-CN')).toBe('股票');
  });

  it('renders the English name under en even when name_cn is set', () => {
    expect(localizedClassName('Equity', '股票', 'en')).toBe('Equity');
  });

  it('en and zh-CN differ for the same class when name_cn is set', () => {
    const en = localizedClassName('Fixed Income', '固定收益', 'en');
    const zh = localizedClassName('Fixed Income', '固定收益', 'zh-CN');
    expect(en).not.toBe(zh);
    expect(en).toBe('Fixed Income');
    expect(zh).toBe('固定收益');
  });

  it('falls back to the English name under zh-CN when name_cn is null (user-created class with no translation)', () => {
    expect(localizedClassName('SMB', null, 'zh-CN')).toBe('SMB');
  });

  it('falls back to the English name under zh-CN when name_cn is undefined', () => {
    expect(localizedClassName('SMB', undefined, 'zh-CN')).toBe('SMB');
  });

  it('falls back to the English name under zh-CN when name_cn is an empty string', () => {
    expect(localizedClassName('SMB', '', 'zh-CN')).toBe('SMB');
  });

  it('never renders a raw null or undefined — falls back to empty string only when name itself is missing', () => {
    expect(localizedClassName(null, null, 'zh-CN')).toBe('');
    expect(localizedClassName(undefined, undefined, 'en')).toBe('');
  });

  it('all six owner-reported top classes differ between en and zh-CN', () => {
    const pairs: Array<[string, string]> = [
      ['Equity', '股票'],
      ['Fixed Income', '固定收益'],
      ['Commodity', '商品'],
      ['Cash', '现金'],
      ['Alternative', '另类投资'],
      ['Real Estate', '房地产'],
    ];
    for (const [en, cn] of pairs) {
      expect(localizedClassName(en, cn, 'en')).toBe(en);
      expect(localizedClassName(en, cn, 'zh-CN')).toBe(cn);
      expect(localizedClassName(en, cn, 'en')).not.toBe(localizedClassName(en, cn, 'zh-CN'));
    }
  });
});
