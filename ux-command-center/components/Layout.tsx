import { BrandMark } from './BrandMark';
import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTheme } from '../src/theme/useTheme';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { useAuth } from '../src/context/useAuth';
import { LogOut } from 'lucide-react';
import { useDemoMode } from '../src/context/useDemoMode';
import { useCurrency } from '../src/context/useCurrency';
import { useLanguage } from '../src/context/useLanguage';
import { authFetch } from '../src/services/authFetch';
import { API_BASE } from '../src/services/api';
import { APP_VERSION_DISPLAY } from '../src/version';

interface LayoutProps {
  children: React.ReactNode;
}

interface NavItemProps {
  icon: string;
  label: string;
  path: string;
  isActive: boolean;
  onClick: () => void;
}

const NavItem: React.FC<NavItemProps> = ({ icon, label, path, isActive, onClick }) => (
  <div
    onClick={onClick}
    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${isActive
      ? 'bg-primary/10 text-primary'
      : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-card-dark hover:text-slate-900 dark:hover:text-white'
      }`}
  >
    <span className={`material-symbols-outlined !text-[20px] ${isActive ? 'filled-icon' : ''}`}>{icon}</span>
    <span className={`text-sm font-medium ${isActive ? 'font-bold' : ''}`}>{label}</span>
    {isActive && <span className="ml-auto flex h-1.5 w-1.5 rounded-full bg-primary"></span>}
  </div>
);

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { mode, toggleMode } = useTheme();
  const { includeNonRebalanceable, toggleNonRebalanceable } = usePortfolioFilter();
  const { logout } = useAuth();
  const { demoMode, toggleDemoMode } = useDemoMode();
  const { currency, setCurrency, usdCnyRate, rateIsFallback, rateAsOf } = useCurrency();
  const { lang, toggleLang, t } = useLanguage();

  const [profileName, setProfileName] = useState('');
  const [profileAvatar, setProfileAvatar] = useState<string | null>(null);

  // One string, three shapes — written out in full rather than assembled from fragments so
  // a translator never has to reason about a value that starts with a space.
  const rateNote = rateIsFallback
    ? t('currency.rateApproxFallback', { rate: usdCnyRate.toFixed(4) })
    : rateAsOf
      ? t('currency.rateApproxAsOf', { rate: usdCnyRate.toFixed(4), date: rateAsOf.slice(0, 10) })
      : t('currency.rateApprox', { rate: usdCnyRate.toFixed(4) });

  useEffect(() => {
    authFetch(`${API_BASE}/settings/profile`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setProfileName(data.display_name || '');
          setProfileAvatar(data.avatar_url || null);
        }
      })
      .catch(() => {});
  }, []);

  // `titleKey` / `labelKey` are `common` namespace dot paths, NOT display strings.
  // (They used to be English literals doubling as translation keys — see ADR-028.)
  const navSections = [
    {
      titleKey: 'section.portfolio',
      items: [
        { icon: 'dashboard', labelKey: 'nav.unifiedPortfolio', path: '/' },
        { icon: 'trending_up', labelKey: 'nav.performance', path: '/performance' },
        { icon: 'account_balance_wallet', labelKey: 'nav.wealthOS', path: '/wealth' },
        { icon: 'analytics', labelKey: 'nav.riskMatrix', path: '/risk' },
      ],
    },
    {
      titleKey: 'section.reports',
      items: [
        { icon: 'explore', labelKey: 'nav.compassReport', path: '/compass' },
        { icon: 'account_balance', labelKey: 'nav.balanceSheet', path: '/balance-sheet' },
        { icon: 'payments', labelKey: 'nav.incomeExpense', path: '/income-expense' },
        { icon: 'waterfall_chart', labelKey: 'nav.monthlyAttribution', path: '/monthly-attribution' },
        { icon: 'monitoring', labelKey: 'nav.marketSentiment', path: '/market-sentiment' },
        { icon: 'price_check', labelKey: 'nav.valuation', path: '/valuation' },
        { icon: 'insights', labelKey: 'nav.forecast', path: '/analytics' },
      ],
    },
    {
      titleKey: 'section.aiAdvisors',
      items: [
        { icon: 'smart_toy', labelKey: 'nav.aiAdvisor', path: '/ai-advisor' },
        { icon: 'verified', labelKey: 'nav.reviewCenter', path: '/verify' },
        { icon: 'gavel', labelKey: 'nav.decisionHub', path: '/decisions' },
        { icon: 'warning', labelKey: 'nav.valueTrapReviews', path: '/value-trap-reviews' },
        { icon: 'policy', labelKey: 'nav.strategyAlignment', path: '/strategy' },
      ],
    },
    {
      titleKey: 'section.operations',
      items: [
        { icon: 'verified_user', labelKey: 'nav.portfolioAudit', path: '/audit' },
        { icon: 'fact_check', labelKey: 'nav.assetClassAudit', path: '/asset-class-audit' },
        { icon: 'inventory_2', labelKey: 'nav.assetAudit', path: '/asset-audit' },
        { icon: 'folder_managed', labelKey: 'nav.assetCaseFiles', path: '/asset-case-file' },
        { icon: 'receipt_long', labelKey: 'nav.transactions', path: '/transactions' },
        { icon: 'sell', labelKey: 'nav.cashFlowClassification', path: '/cash-flow-classification' },
      ],
    },
    {
      titleKey: 'section.management',
      items: [
        { icon: 'account_tree', labelKey: 'nav.taxonomy', path: '/taxonomy' },
        { icon: 'rule', labelKey: 'nav.rules', path: '/classification-rules' },
        { icon: 'layers', labelKey: 'nav.tierAudit', path: '/tier-audit' },
        { icon: 'tune', labelKey: 'nav.riskProfiles', path: '/risk-profiles' },
      ],
    },
    {
      titleKey: 'section.system',
      items: [
        { icon: 'smart_toy', labelKey: 'nav.aiModels', path: '/ai-models' },
        { icon: 'edit_note', labelKey: 'nav.aiPrompts', path: '/ai-prompts' },
        { icon: 'database', labelKey: 'nav.dataSources', path: '/data-sources' },
        { icon: 'upload_file', labelKey: 'nav.syncHistory', path: '/import' },
        { icon: 'settings', labelKey: 'nav.settings', path: '/settings' },
      ],
    },
  ];

  return (
    <div className="flex h-screen w-full bg-background-light dark:bg-background-dark text-slate-900 dark:text-slate-100">
      <aside className="w-64 flex-shrink-0 flex flex-col border-r border-slate-200 dark:border-border-dark bg-white dark:bg-sidebar-dark z-20">
        <div className="p-6 flex flex-col gap-1">
          <div className="flex items-center gap-3 mb-2">
            <div className="bg-primary p-1.5 rounded-lg shadow-lg shadow-primary/30">
              <BrandMark className="size-6 text-white" />
            </div>
            <h1 className="text-lg font-bold tracking-tight">{t('appName')}</h1>
          </div>
          <div className="flex items-center justify-between">
            <p className="text-slate-500 dark:text-slate-500 text-[10px] font-mono pl-1">{APP_VERSION_DISPLAY}</p>
            <button
              type="button"
              onClick={toggleMode}
              aria-label={mode === 'day' ? t('theme.switchToNight') : t('theme.switchToDay')}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 dark:border-border-dark px-2 py-1 text-[10px] font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-card-dark transition-colors"
            >
              <span className="material-symbols-outlined !text-[14px]">
                {mode === 'day' ? 'dark_mode' : 'light_mode'}
              </span>
              <span>{mode === 'day' ? t('theme.night') : t('theme.day')}</span>
            </button>
          </div>
        </div>

        <nav className="flex-1 px-4 space-y-1 overflow-y-auto">
          {navSections.map((section, si) => (
            <div key={si}>
              <p className="px-3 text-[10px] font-bold text-slate-400 dark:text-slate-600 uppercase tracking-widest mb-2 mt-6">
                {t(section.titleKey)}
              </p>
              {section.items.map((item) => (
                <NavItem
                  key={item.path}
                  icon={item.icon}
                  label={t(item.labelKey)}
                  path={item.path}
                  isActive={location.pathname === item.path}
                  onClick={() => navigate(item.path)}
                />
              ))}
            </div>
          ))}
        </nav>

        {/* Compact icon-toggle row: Language · Currency · Illiquid · Demo */}
        <div className="px-4 py-3">
          <div className="bg-slate-50 dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark px-3 py-2">
            <div className="flex items-center justify-around gap-1">
              {/* Language toggle */}
              {/* The label/aria/title of a language switcher are inherently "the other
                  language" — that is now expressed by the catalog (each locale states what
                  clicking does) rather than by a ternary on `lang`. Rendered output is
                  unchanged in both locales. */}
              <button
                type="button"
                aria-label={t('settings.toggleLanguageAria')}
                aria-pressed={lang === 'zh-CN'}
                title={t('settings.toggleLanguageTitle')}
                onClick={toggleLang}
                className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-primary ${
                  lang === 'zh-CN'
                    ? 'text-primary bg-primary/10'
                    : 'text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                <span className="material-symbols-outlined !text-[18px]">translate</span>
                <span className="text-[9px] font-semibold leading-none">{t('settings.languageCode')}</span>
              </button>

              {/* Currency toggle */}
              <button
                type="button"
                aria-label={currency === 'CNY' ? t('currency.switchToUsd') : t('currency.switchToCny')}
                aria-pressed={currency === 'USD'}
                title={currency === 'USD' ? rateNote : t('currency.switchToUsd')}
                onClick={() => setCurrency(currency === 'CNY' ? 'USD' : 'CNY')}
                className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-primary ${
                  currency === 'USD'
                    ? 'text-primary bg-primary/10'
                    : 'text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                <span className="material-symbols-outlined !text-[18px]">currency_exchange</span>
                <span className="text-[9px] font-semibold leading-none">{currency === 'CNY' ? '¥' : '$'}</span>
              </button>

              {/* Include Illiquid toggle */}
              <button
                type="button"
                role="switch"
                aria-checked={includeNonRebalanceable}
                aria-label={t('filters.includeIlliquidAria')}
                title={t('filters.includeIlliquidTitle')}
                onClick={toggleNonRebalanceable}
                className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-primary ${
                  includeNonRebalanceable
                    ? 'text-primary bg-primary/10'
                    : 'text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                <span className="material-symbols-outlined !text-[18px]">account_balance</span>
                <span className="text-[9px] font-semibold leading-none">{t('filters.illiquid')}</span>
              </button>

              {/* Demo Mode toggle */}
              <button
                type="button"
                role="switch"
                aria-checked={demoMode}
                aria-label={t('filters.demoModeAria')}
                title={t('filters.demoModeTitle')}
                onClick={toggleDemoMode}
                className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-amber-400 ${
                  demoMode
                    ? 'text-amber-500 bg-amber-500/10'
                    : 'text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700'
                }`}
              >
                <span className="material-symbols-outlined !text-[18px]">visibility_off</span>
                <span className="text-[9px] font-semibold leading-none">{t('filters.demo')}</span>
              </button>
            </div>
            {currency === 'USD' && (
              <p className="text-[9px] text-amber-500 dark:text-amber-400 mt-1.5 text-center leading-tight">
                {rateNote}
              </p>
            )}
          </div>
        </div>

        <div className="p-4 border-t border-slate-200 dark:border-border-dark">
          <div
            className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-slate-100 dark:hover:bg-card-dark transition-colors cursor-pointer group"
            onClick={() => navigate('/settings')}
          >
            <div className="size-8 rounded-full bg-primary/15 dark:bg-primary/20 flex items-center justify-center overflow-hidden border border-primary/20 dark:border-primary/30 shrink-0">
              {profileAvatar ? (
                <img className="object-cover size-full" src={profileAvatar} alt={t('profile.avatarAlt')} />
              ) : (
                <span className="text-sm font-bold text-primary select-none">
                  {profileName ? profileName.charAt(0).toUpperCase() : '?'}
                </span>
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold truncate text-slate-700 dark:text-slate-200">
                {profileName || t('profile.setYourName')}
              </p>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 group-hover:text-primary transition-colors">
                {t('nav.settings')}
              </p>
            </div>
            <span className="material-symbols-outlined text-slate-400 text-[18px] group-hover:text-primary transition-colors">
              settings
            </span>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); logout(); }}
              title={t('actions.logout')}
              className="text-slate-400 hover:text-red-400 transition-colors"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0 overflow-y-auto relative scroll-smooth">
        {children}
      </main>
    </div>
  );
};
