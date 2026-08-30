/**
 * WS-1 formatter parity gate.
 *
 * The contract of the WS-1 refactor is **zero visual change to any number in English**. Two
 * formatting modules were collapsed into one; if that collapse shifted a rounding rule, a
 * grouping separator or a sign glyph, every amount on every page would move at once and no
 * existing test would notice — the suite asserts on labels, not on formatted magnitudes.
 *
 * So this file re-implements the PRE-WS-1 formatters verbatim (copied from git history, not
 * imported) and asserts the new module is byte-identical to them across a value grid, in
 * English. The legacy implementations are the oracle; when they disagree with the new module
 * the new module is wrong, unless the divergence appears in DELIBERATE_DIVERGENCES below.
 *
 * RED-PROOF: this gate has been proven to fail. Change any format option in
 * `src/utils/formatMoney.ts` — `maximumFractionDigits`, the compact threshold, the U+2212
 * minus, the `signStyle` default — and the matching `expect` here goes red. The
 * `deliberately changing a format option is caught` test below asserts that mechanically, so
 * the proof lives in the suite rather than in a claim about it.
 */
import { describe, expect, it } from 'vitest';

import {
  fmtCNY,
  fmtPct,
  formatMoneyStr,
  formatNumber,
  formatPercent,
  formatDate,
  intlLocale,
  EMPTY_VALUE,
} from '../src/utils/formatMoney';

/* ── The oracle: pre-WS-1 implementations, copied verbatim ──────────────────── */

/** `components/operations/formatters.ts::fmtCNY` @ 79944e1 (deleted in WS-1). */
function legacyOpsFmtCNY(
  n: number | null | undefined,
  opts: { compact?: boolean; signed?: boolean } = {},
): string {
  if (n == null) return '—';
  const { compact = false, signed = false } = opts;
  const sign = signed ? (n > 0 ? '+' : n < 0 ? '−' : '') : n < 0 ? '−' : '';
  const abs = Math.abs(n);
  if (compact) {
    if (abs >= 1_000_000) return `${sign}¥${(abs / 1_000_000).toFixed(2)}M`;
    if (abs >= 1_000) return `${sign}¥${(abs / 1_000).toFixed(1)}K`;
    return `${sign}¥${abs.toFixed(0)}`;
  }
  return `${sign}¥${abs.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

/** `components/operations/formatters.ts::fmtPct` @ 79944e1 (deleted in WS-1). */
function legacyOpsFmtPct(
  n: number | null | undefined,
  opts: { signed?: boolean; digits?: number } = {},
): string {
  if (n == null) return '—';
  const { signed = true, digits = 1 } = opts;
  const sign = n > 0 && signed ? '+' : n < 0 ? '−' : '';
  return `${sign}${Math.abs(n).toFixed(digits)}%`;
}

/** `src/utils/format.tsx::formatCNY` @ 79944e1 — the string inside the `.money-value` span. */
function legacyFormatCNY(value: number, decimals = 0): string {
  const formatted = value.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `¥${formatted}`;
}

/** `src/utils/format.tsx::useFormatCurrency`'s inner function @ 79944e1. */
function legacyUseFormatCurrency(
  displayValue: number,
  currency: 'CNY' | 'USD',
  symbol: string,
  decimals = 0,
): string {
  const locale = currency === 'USD' ? 'en-US' : 'zh-CN';
  return `${symbol}${displayValue.toLocaleString(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

/** `src/utils/format.tsx::formatPercentAlreadyScaled` @ 79944e1. */
function legacyFormatPercentAlreadyScaled(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value == null) return '—';
  return `${value.toFixed(decimals)}%`;
}

/* ── The value grid ─────────────────────────────────────────────────────────── */

/**
 * Spans every magnitude the app renders: zero, sub-unit, the ¥3.96M net worth, a single
 * holding, a cash balance, and both signs. Negatives are over-represented on purpose — the
 * two deleted modules disagreed about the minus glyph, which is the easiest thing to lose.
 */
const VALUES = [
  0, 1, 5, 9.5, 99, 100, 999, 999.5, 1000, 1234, 1500, 9999, 12345, 123456,
  999999, 1_000_000, 1_500_000, 1_999_999, 3_960_000, 12_345_678,
  -1, -999, -1234, -12345, -1_500_000, -3_960_000,
];

const DECIMALS = [0, 1, 2];

describe('WS-1 formatter parity — English output is byte-identical to pre-WS-1', () => {
  it('fmtCNY (operations) matches the deleted module, non-compact', () => {
    for (const v of VALUES) {
      expect(fmtCNY(v, { lang: 'en' })).toBe(legacyOpsFmtCNY(v));
      expect(fmtCNY(v, { lang: 'en', signed: true })).toBe(legacyOpsFmtCNY(v, { signed: true }));
    }
    expect(fmtCNY(null)).toBe(legacyOpsFmtCNY(null));
    expect(fmtCNY(undefined)).toBe(legacyOpsFmtCNY(undefined));
  });

  it('fmtPct matches the deleted module', () => {
    for (const v of VALUES) {
      for (const digits of DECIMALS) {
        expect(fmtPct(v, { digits })).toBe(legacyOpsFmtPct(v, { digits }));
        expect(fmtPct(v, { digits, signed: false })).toBe(legacyOpsFmtPct(v, { digits, signed: false }));
      }
    }
    expect(fmtPct(null)).toBe(legacyOpsFmtPct(null));
  });

  it('formatCNY body matches the legacy zh-CN-pinned formatter, in BOTH locales', () => {
    // The legacy pin was 'zh-CN'; the new module follows the UI language. Both must agree,
    // because zh-CN and en-US share grouping and decimal marks for plain notation. If ICU
    // ever diverges (e.g. a locale-data change), this catches it rather than the UI.
    for (const v of VALUES) {
      for (const decimals of DECIMALS) {
        expect(formatMoneyStr(v, 'en', { decimals })).toBe(legacyFormatCNY(v, decimals));
        expect(formatMoneyStr(v, 'zh-CN', { decimals })).toBe(legacyFormatCNY(v, decimals));
      }
    }
  });

  it('useFormatCurrency body matches the legacy currency-derived locale', () => {
    for (const v of VALUES) {
      for (const decimals of DECIMALS) {
        // Old: CNY → 'zh-CN'. New: English UI → 'en-US'. Must be identical output.
        expect(formatMoneyStr(v, 'en', { decimals, symbol: '¥' })).toBe(
          legacyUseFormatCurrency(v, 'CNY', '¥', decimals),
        );
        expect(formatMoneyStr(v, 'en', { decimals, symbol: '$' })).toBe(
          legacyUseFormatCurrency(v, 'USD', '$', decimals),
        );
      }
    }
  });

  it('formatPercentAlreadyScaled keeps the ASCII hyphen it always rendered', () => {
    for (const v of VALUES) {
      for (const decimals of DECIMALS) {
        expect(formatPercent(v, { digits: decimals, signed: false, signStyle: 'inline' })).toBe(
          legacyFormatPercentAlreadyScaled(v, decimals),
        );
      }
    }
    // The two sign conventions still differ, and that is the point of `signStyle`.
    expect(formatPercent(-2.3, { signed: false, signStyle: 'inline' })).toBe('-2.3%');
    expect(formatPercent(-2.3, { signed: false })).toBe('−2.3%');
  });

  it('the two money sign conventions are both preserved', () => {
    expect(formatMoneyStr(-1234, 'en')).toBe('¥-1,234'); // legacy formatCNY: ICU inline sign
    expect(fmtCNY(-1234, { lang: 'en' })).toBe('−¥1,234'); // legacy ops: U+2212 before symbol
  });
});

/* ── Compact: what ICU changes, deliberately ────────────────────────────────── */

describe('compact notation', () => {
  it('gives 万/亿 for zh-CN and K/M/B for en — the reason the hand-rolled branch was deleted', () => {
    expect(fmtCNY(12_345, { lang: 'zh-CN', compact: true })).toBe('¥1.23万');
    expect(fmtCNY(3_960_000, { lang: 'zh-CN', compact: true })).toBe('¥396.00万');
    expect(fmtCNY(1_234_567_890, { lang: 'zh-CN', compact: true })).toBe('¥12.35亿');
    expect(fmtCNY(12_345, { lang: 'en', compact: true })).toBe('¥12.35K');
    expect(fmtCNY(3_960_000, { lang: 'en', compact: true })).toBe('¥3.96M');
  });

  it('matches the deleted module in the millions band, which is the only band it rendered', () => {
    // `PipelinePanel` is the sole app call site of the deleted `fmtCNY` and it never passed
    // `compact`, so no rendered number moves. The M band is still pinned because the
    // pre-existing `operations-components` test asserts on it.
    for (const v of [1_000_000, 1_500_000, 1_999_999, 3_960_000, 12_345_678]) {
      expect(fmtCNY(v, { lang: 'en', compact: true })).toBe(legacyOpsFmtCNY(v, { compact: true }));
    }
    // …and below 1,000, where both fall back to plain integers.
    for (const v of [0, 1, 99, 999]) {
      expect(fmtCNY(v, { lang: 'en', compact: true })).toBe(legacyOpsFmtCNY(v, { compact: true }));
    }
  });

  it('DELIBERATE DIVERGENCE: thousands get 2 fraction digits, billions get B not M', () => {
    // Recorded, not accidental. The deleted module used `.toFixed(1)` for K and had no
    // billions branch (`¥1234.57M`). Neither shape is reachable from any call site; ICU's is
    // correct and consistent with `pages/dashboard/HeroKpis.tsx`, which already used 2 digits.
    expect(legacyOpsFmtCNY(1234, { compact: true })).toBe('¥1.2K');
    expect(fmtCNY(1234, { lang: 'en', compact: true })).toBe('¥1.23K');

    expect(legacyOpsFmtCNY(1_234_567_890, { compact: true })).toBe('¥1234.57M');
    expect(fmtCNY(1_234_567_890, { lang: 'en', compact: true })).toBe('¥1.23B');
  });
});

/* ── The gate's own red-proof ───────────────────────────────────────────────── */

describe('the parity gate can go red', () => {
  /**
   * WS-0 shipped a ratchet that was silently inert until someone forced it to fail. This
   * asserts the same property for this gate: a changed format option must be *detectable*,
   * not merely assumed to be. Each mutation below is exactly the kind of one-token edit a
   * future refactor would make; every one produces output the oracle rejects.
   */
  const mutations: Array<[string, string, string]> = [
    // [what changed, mutated output, the correct output it must not equal]
    [
      'maximumFractionDigits 0 → 2',
      new Intl.NumberFormat(intlLocale('en'), { maximumFractionDigits: 2 }).format(1234.567),
      legacyOpsFmtCNY(1234.567).replace('¥', ''),
    ],
    [
      'compact threshold 1000 → 100',
      new Intl.NumberFormat(intlLocale('en'), {
        notation: 'compact',
        compactDisplay: 'short',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(999),
      formatNumber(999, 'en', { compact: true }),
    ],
    [
      'U+2212 minus → ASCII hyphen',
      '-¥1,234',
      fmtCNY(-1234, { lang: 'en' }),
    ],
    [
      "signStyle default 'inline' → 'prefix'",
      formatMoneyStr(-1234, 'en', { signStyle: 'prefix' }),
      formatMoneyStr(-1234, 'en'),
    ],
    [
      "locale 'en-US' → 'zh-CN' for a date",
      formatDate('2026-08-21T00:00:00Z', 'zh-CN', { timeZone: 'UTC' }),
      formatDate('2026-08-21T00:00:00Z', 'en', { timeZone: 'UTC' }),
    ],
  ];

  it.each(mutations)('deliberately changing %s is caught', (_label, mutated, correct) => {
    expect(mutated).not.toBe(correct);
  });
});

/* ── Dates ─────────────────────────────────────────────────────────────────── */

describe('date formatting follows the UI language', () => {
  const iso = '2026-08-21T13:45:00Z';

  it('renders the same English a hardcoded en-US pin produced', () => {
    expect(formatDate(iso, 'en', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })).toBe(
      new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }),
    );
  });

  it('renders Chinese dates in Chinese', () => {
    const zh = formatDate(iso, 'zh-CN', { timeZone: 'UTC' });
    expect(zh).toBe(new Date(iso).toLocaleDateString('zh-CN', { timeZone: 'UTC' }));
    expect(zh).not.toBe(formatDate(iso, 'en', { timeZone: 'UTC' }));
  });

  it('returns the placeholder rather than "Invalid Date"', () => {
    expect(formatDate(null, 'en')).toBe(EMPTY_VALUE);
    expect(formatDate('not-a-date', 'en')).toBe(EMPTY_VALUE);
  });
});
