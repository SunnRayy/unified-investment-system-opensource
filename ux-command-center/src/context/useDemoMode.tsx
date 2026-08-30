import React, { createContext, useContext, useState, useEffect } from 'react';

const STORAGE_KEY = 'uis-demo-mode';

interface DemoModeContextType {
  demoMode: boolean;
  toggleDemoMode: () => void;
}

const DemoModeContext = createContext<DemoModeContextType | undefined>(undefined);

export const DemoModeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [demoMode, setDemoMode] = useState(() => localStorage.getItem(STORAGE_KEY) === 'true');

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(demoMode));
    document.body.dataset.demo = String(demoMode);
  }, [demoMode]);

  // Sync body dataset on mount (before first toggle)
  useEffect(() => {
    document.body.dataset.demo = String(demoMode);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleDemoMode = () => setDemoMode(prev => !prev);

  return (
    <DemoModeContext.Provider value={{ demoMode, toggleDemoMode }}>
      {children}
    </DemoModeContext.Provider>
  );
};

export function useDemoMode(): DemoModeContextType {
  const ctx = useContext(DemoModeContext);
  if (!ctx) throw new Error('useDemoMode must be used within DemoModeProvider');
  return ctx;
}
