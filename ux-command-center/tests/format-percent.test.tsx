/**
 * Regression test for BUG 1 (2026-07-25 owner UI review):
 * "Savings Rate" rendered as 1410.0% because Analytics.tsx multiplied an
 * ALREADY-percent backend value (cash_flow.py::calculate_trends's
 * `savings_rate`, e.g. 14.1) by 100 a second time.
 *
 * formatPercentAlreadyScaled must render the input as-is (one decimal + %),
 * never re-scaled.
 */
import { describe, expect, it } from 'vitest';
import { formatPercentAlreadyScaled } from '../src/utils/format';

describe('formatPercentAlreadyScaled', () => {
  it('renders an already-percent value directly, not scaled by 100 again', () => {
    // This is the exact class of input that produced "1410.0%" when the
    // caller did `(value * 100).toFixed(1)` on an already-percent value.
    expect(formatPercentAlreadyScaled(14.1)).toBe('14.1%');
  });

  it('never produces a value an order of magnitude too large for realistic inputs', () => {
    const value = 14.1;
    const rendered = formatPercentAlreadyScaled(value);
    expect(rendered).not.toBe('1410.0%');
    expect(rendered).not.toContain('1410');
  });

  it('handles 0 and negative surplus rates', () => {
    expect(formatPercentAlreadyScaled(0)).toBe('0.0%');
    expect(formatPercentAlreadyScaled(-5.3)).toBe('-5.3%');
  });

  it('handles null/undefined as em-dash placeholder', () => {
    expect(formatPercentAlreadyScaled(null)).toBe('—');
    expect(formatPercentAlreadyScaled(undefined)).toBe('—');
  });

  it('respects a custom decimals argument', () => {
    expect(formatPercentAlreadyScaled(14.15, 2)).toBe('14.15%');
  });
});
