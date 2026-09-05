import { describe, expect, it } from 'vitest';
import { DAY_THEME_COLORS } from '../src/theme/themeClasses';

describe('day theme design tokens', () => {
  it('maps to approved light design system values', () => {
    expect(DAY_THEME_COLORS.background).toBe('#F9FAFB');
    expect(DAY_THEME_COLORS.card).toBe('#FFFFFF');
    expect(DAY_THEME_COLORS.primary).toBe('#3b82f6');
    expect(DAY_THEME_COLORS.success).toBe('#22c55e');
    expect(DAY_THEME_COLORS.error).toBe('#ef4444');
    expect(DAY_THEME_COLORS.neutral).toBe('#eab308');
    expect(DAY_THEME_COLORS.investment).toBe('#6366f1');
  });
});
