import React, { createContext, useContext, useState, ReactNode } from 'react';
import { authFetch } from '../services/authFetch';

const AUTH_STORAGE_KEY = 'uis-auth-token';

interface AuthContextType {
  token: string | null;
  isAuthenticated: boolean;
  login: (token: string) => void;
  logout: () => void;
  changePassword: (currentPw: string, newPw: string) => Promise<string>;
  logoutAll: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(AUTH_STORAGE_KEY));

  const login = (newToken: string) => {
    localStorage.setItem(AUTH_STORAGE_KEY, newToken);
    setToken(newToken);
  };

  const logout = () => {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    setToken(null);
  };

  const changePassword = async (currentPw: string, newPw: string): Promise<string> => {
    const res = await authFetch('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? 'Password change failed');
    }
    const { token: newToken } = await res.json();
    login(newToken);
    return newToken;
  };

  const logoutAll = async (): Promise<void> => {
    await authFetch('/api/auth/logout-all', { method: 'POST' });
    logout();
  };

  return (
    <AuthContext.Provider value={{ token, isAuthenticated: !!token, login, logout, changePassword, logoutAll }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
