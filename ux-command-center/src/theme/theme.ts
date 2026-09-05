export type ThemeMode = 'day' | 'night';

export const THEME_STORAGE_KEY = 'uis-theme';

export const isThemeMode = (value: unknown): value is ThemeMode =>
  value === 'day' || value === 'night';
