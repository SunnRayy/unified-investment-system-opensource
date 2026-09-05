import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { isThemeMode, THEME_STORAGE_KEY, ThemeMode } from './theme';

interface ThemeContextValue {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const getInitialTheme = (): ThemeMode => {
  if (typeof window === 'undefined') {
    return 'day';
  }

  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  return isThemeMode(saved) ? saved : 'day';
};

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [mode, setMode] = useState<ThemeMode>(() => getInitialTheme());

  useEffect(() => {
    window.localStorage.setItem(THEME_STORAGE_KEY, mode);
    document.documentElement.classList.toggle('dark', mode === 'night');
  }, [mode]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      setMode,
      toggleMode: () => setMode(mode === 'day' ? 'night' : 'day'),
    }),
    [mode]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

export const useTheme = (): ThemeContextValue => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};
