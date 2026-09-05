import { BrandMark } from '../components/BrandMark';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../src/context/useAuth';

const API_BASE = '/api';

export function LoginPage() {
  const { t } = useTranslation('common');
  const { login } = useAuth();
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (response.ok) {
        const data = await response.json();
        login(data.token);
      } else {
        setError(t('login.incorrectPassword'));
      }
    } catch {
      setError(t('login.connectionError'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F9FAFB] flex items-center justify-center p-4">
      <div className="w-full max-w-[420px] bg-white rounded-2xl border-2 border-slate-200 shadow-sm px-[60px] py-10 flex flex-col items-center">

        {/* Logo */}
        <div className="w-20 h-20 bg-primary rounded-2xl flex items-center justify-center mb-10 shrink-0">
          <BrandMark className="w-12 h-12 text-white" title="Huinsight" />
        </div>

        {/* Header */}
        <div className="w-full mb-8">
          <h1 className="text-xl font-bold text-slate-900 tracking-tight mb-1.5">{t('login.authenticate')}</h1>
          <p className="text-sm text-slate-400 font-medium">{t('login.subtitle')}</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="w-full space-y-3">
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder={t('login.passwordPlaceholder')}
            required
            autoFocus
            className="w-full h-9 bg-gray-50 border-2 border-slate-200 rounded-lg px-3 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors"
          />

          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2">
              <svg className="w-4 h-4 text-red-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
              </svg>
              <p className="text-xs text-red-600 font-medium">{error}</p>
            </div>
          )}

          <div className="pt-1">
            <button
              type="submit"
              disabled={loading || !password}
              className="w-full h-10 bg-blue-500 hover:bg-blue-600 active:bg-blue-700 disabled:bg-blue-300 disabled:cursor-not-allowed text-white font-semibold rounded-lg text-sm transition-colors flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <svg className="animate-spin w-4 h-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  {t('login.signingIn')}
                </>
              ) : (
                t('login.signIn')
              )}
            </button>
          </div>
        </form>

        <p className="mt-8 text-[11px] text-slate-300 text-center select-none">
          {t('login.footer')}
        </p>
      </div>
    </div>
  );
}
