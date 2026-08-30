import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE } from '../../src/services/api';

function getStoredToken(): string | null {
  return localStorage.getItem('uis-auth-token');
}

async function authedFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getStoredToken();
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers as Record<string, string> | undefined),
    },
  });
}

function ProfileCard() {
  const { t } = useTranslation(['system', 'common']);
  const [displayName, setDisplayName] = useState('');
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    authedFetch('/settings/profile')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setDisplayName(data.display_name || '');
          setAvatarUrl(data.avatar_url || null);
        }
      })
      .catch(() => {});
  }, []);

  const handleAvatarFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 200_000) {
      setStatus({ type: 'error', message: t('accountManager.imageTooLarge') });
      return;
    }
    const reader = new FileReader();
    reader.onload = ev => {
      setAvatarUrl(ev.target?.result as string);
      setStatus(null);
    };
    reader.readAsDataURL(file);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus(null);
    setLoading(true);
    try {
      const res = await authedFetch('/settings/profile', {
        method: 'PUT',
        body: JSON.stringify({ display_name: displayName, avatar_url: avatarUrl ?? '' }),
      });
      if (res.ok) {
        setStatus({ type: 'success', message: t('accountManager.profileSaved') });
      } else {
        const data = await res.json().catch(() => ({}));
        setStatus({ type: 'error', message: data.detail ?? t('accountManager.saveFailed') });
      }
    } catch {
      setStatus({ type: 'error', message: t('accountManager.networkError') });
    } finally {
      setLoading(false);
    }
  };

  const initials = displayName ? displayName.charAt(0).toUpperCase() : '?';

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark p-5">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-4">{t('accountManager.profile')}</h3>
      <form onSubmit={handleSave} className="space-y-4">
        <div className="flex items-center gap-4">
          <div className="relative shrink-0">
            <div
              className="size-16 rounded-full bg-primary/15 dark:bg-primary/20 border-2 border-primary/20 dark:border-primary/30 flex items-center justify-center overflow-hidden cursor-pointer hover:opacity-80 transition-opacity"
              onClick={() => fileRef.current?.click()}
              title={t('accountManager.clickToChangePhoto')}
            >
              {avatarUrl ? (
                <img className="object-cover size-full" src={avatarUrl} alt={t('common:profile.avatarAlt')} />
              ) : (
                <span className="text-2xl font-bold text-primary select-none">{initials}</span>
              )}
            </div>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="absolute -bottom-1 -right-1 size-6 rounded-full bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 flex items-center justify-center hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors shadow-sm"
              title={t('accountManager.uploadPhoto')}
            >
              <span className="material-symbols-outlined !text-[13px] text-slate-500 dark:text-slate-400">photo_camera</span>
            </button>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleAvatarFile} />
          </div>
          <div className="flex-1 min-w-0">
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">{t('accountManager.displayName')}</label>
            <input
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder={t('accountManager.yourName')}
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
        </div>
        {avatarUrl && (
          <button
            type="button"
            onClick={() => { setAvatarUrl(null); setStatus(null); }}
            className="text-xs text-slate-400 hover:text-red-500 transition-colors"
          >
            {t('accountManager.removePhoto')}
          </button>
        )}
        {status && (
          <p className={`text-xs ${status.type === 'success' ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
            {status.message}
          </p>
        )}
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {loading && <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>}
          {t('accountManager.saveProfile')}
        </button>
      </form>
    </div>
  );
}

export function AccountManager() {
  const { t } = useTranslation('system');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [changePwStatus, setChangePwStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [changePwLoading, setChangePwLoading] = useState(false);

  const [logoutStatus, setLogoutStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [logoutLoading, setLogoutLoading] = useState(false);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setChangePwStatus(null);
    if (newPassword !== confirmPassword) {
      setChangePwStatus({ type: 'error', message: t('accountManager.passwordsDoNotMatch') });
      return;
    }
    if (newPassword.includes('.')) {
      setChangePwStatus({ type: 'error', message: t('accountManager.passwordNoPeriod') });
      return;
    }
    setChangePwLoading(true);
    try {
      const res = await authedFetch('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.token) localStorage.setItem('uis-auth-token', data.token);
        setChangePwStatus({ type: 'success', message: t('accountManager.passwordUpdated') });
        setCurrentPassword('');
        setNewPassword('');
        setConfirmPassword('');
      } else {
        const data = await res.json().catch(() => ({}));
        setChangePwStatus({ type: 'error', message: data.detail ?? t('accountManager.passwordChangeFailed') });
      }
    } catch {
      setChangePwStatus({ type: 'error', message: t('accountManager.networkError') });
    } finally {
      setChangePwLoading(false);
    }
  };

  const handleLogoutAll = async () => {
    setLogoutStatus(null);
    setLogoutLoading(true);
    try {
      const res = await authedFetch('/auth/logout-all', { method: 'POST' });
      if (res.ok) {
        setLogoutStatus({ type: 'success', message: t('accountManager.allDevicesLoggedOut') });
      } else {
        const data = await res.json().catch(() => ({}));
        setLogoutStatus({ type: 'error', message: data.detail ?? t('accountManager.logoutFailed') });
      }
    } catch {
      setLogoutStatus({ type: 'error', message: t('accountManager.networkError') });
    } finally {
      setLogoutLoading(false);
    }
  };

  return (
    <>
      <ProfileCard />

      {/* Change Password */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark p-5">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-4">{t('accountManager.changePassword')}</h3>
        <form onSubmit={handleChangePassword} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">{t('accountManager.currentPassword')}</label>
            <input
              type="password"
              value={currentPassword}
              onChange={e => setCurrentPassword(e.target.value)}
              required
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">{t('accountManager.newPassword')}</label>
            <input
              type="password"
              value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              required
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">{t('accountManager.confirmNewPassword')}</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              required
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          {changePwStatus && (
            <p className={`text-xs ${changePwStatus.type === 'success' ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
              {changePwStatus.message}
            </p>
          )}
          <button
            type="submit"
            disabled={changePwLoading}
            className="px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            {changePwLoading && <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>}
            {t('accountManager.updatePassword')}
          </button>
        </form>
      </div>

      {/* Log Out All Devices */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('accountManager.logOutAllDevices')}</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
              {t('accountManager.logOutAllDevicesHint')}
            </p>
            {logoutStatus && (
              <p className={`text-xs mt-2 ${logoutStatus.type === 'success' ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                {logoutStatus.message}
              </p>
            )}
          </div>
          <button
            onClick={handleLogoutAll}
            disabled={logoutLoading}
            className="shrink-0 px-4 py-2 text-sm font-semibold rounded-lg border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            {logoutLoading && <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>}
            {t('accountManager.logOutAllDevicesBtn')}
          </button>
        </div>
      </div>
    </>
  );
}
