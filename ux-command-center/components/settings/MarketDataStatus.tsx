import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  MarketDataRefreshResult,
  MarketDataProvider,
  MarketDataStatusResponse,
  SettingsAPI,
} from '../../src/services/api';

type Tx = (key: string, opts?: Record<string, unknown>) => string;

function relativeTime(t: Tx, isoString: string | null): string {
  if (!isoString) return t('marketDataStatus.never');
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 60000) return t('marketDataStatus.justNow');
  if (diff < 3600000) return t('marketDataStatus.minutesAgo', { count: Math.floor(diff / 60000) });
  if (diff < 86400000) return t('marketDataStatus.hoursAgo', { count: Math.floor(diff / 3600000) });
  return t('marketDataStatus.daysAgo', { count: Math.floor(diff / 86400000) });
}

function stalenessMeta(t: Tx): Record<
  MarketDataStatusResponse['staleness'],
  { label: string; dotClass: string; badgeClass: string }
> {
  return {
    fresh: {
      label: t('marketDataStatus.staleness.fresh'),
      dotClass: 'bg-emerald-500',
      badgeClass: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
    },
    aging: {
      label: t('marketDataStatus.staleness.aging'),
      dotClass: 'bg-amber-500',
      badgeClass: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400',
    },
    stale: {
      label: t('marketDataStatus.staleness.stale'),
      dotClass: 'bg-red-500',
      badgeClass: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
    },
    never: {
      label: t('marketDataStatus.staleness.never'),
      dotClass: 'bg-slate-400',
      badgeClass: 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300',
    },
  };
}

function marketLabels(t: Tx): Record<string, string> {
  return {
    us: t('marketDataStatus.market.us'),
    cn_fund: t('marketDataStatus.market.cnFund'),
  };
}

interface MarketDataStatusProps {
  refreshTrigger?: number;
}

function summaryText(t: Tx, status: MarketDataStatusResponse | null): string {
  if (!status?.last_refresh) return t('marketDataStatus.noRefreshRecorded');
  const { refreshed, skipped, errors } = status.last_refresh;
  return t('marketDataStatus.summary', { refreshed, skipped, errors });
}

function marketLabel(t: Tx, provider: MarketDataProvider): string {
  return marketLabels(t)[provider.market] ?? provider.market;
}

function marketName(t: Tx, market: string): string {
  return marketLabels(t)[market] ?? market;
}

function formatPrice(value: number): string {
  return Number.parseFloat(Number(value).toFixed(4)).toString();
}

function hasRefreshDetails(lastRefresh: MarketDataRefreshResult | null | undefined): boolean {
  if (!lastRefresh) return false;
  return Boolean(
    (lastRefresh.refreshed_assets?.length ?? 0) > 0 ||
    (lastRefresh.error_assets?.length ?? 0) > 0 ||
    (lastRefresh.skipped_assets?.length ?? 0) > 0 ||
    Object.keys(lastRefresh.fx_rates ?? {}).length > 0
  );
}

export const MarketDataStatus: React.FC<MarketDataStatusProps> = ({ refreshTrigger }) => {
  const { t } = useTranslation('system');
  const [status, setStatus] = useState<MarketDataStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await SettingsAPI.getMarketDataStatus();
      setStatus(data);
    } catch {
      setError(t('marketDataStatus.loadError'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadStatus();
    const intervalId = window.setInterval(() => {
      void loadStatus();
    }, 60000);
    return () => window.clearInterval(intervalId);
  }, [loadStatus, refreshTrigger]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      await SettingsAPI.refreshMarketData();
      const data = await SettingsAPI.getMarketDataStatus();
      setStatus(data);
    } catch {
      setError(t('marketDataStatus.refreshError'));
    } finally {
      setRefreshing(false);
    }
  }, [t]);

  const badgeMeta = stalenessMeta(t)[status?.staleness ?? 'never'];
  const refreshTime = relativeTime(t, status?.last_refresh?.timestamp ?? null);
  const lastRefresh = status?.last_refresh ?? null;
  const detailToggleVisible = hasRefreshDetails(lastRefresh);

  return (
    <section className="border border-slate-200 dark:border-border-dark rounded-xl bg-white dark:bg-card-dark mb-4">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="material-symbols-outlined !text-[20px] text-slate-400 dark:text-slate-500">
          price_change
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-slate-800 dark:text-slate-100">{t('marketDataStatus.title')}</div>
          <div className="text-xs mt-0.5 text-slate-500 dark:text-slate-400">
            {status ? t('marketDataStatus.providerGroupsTracked', { count: status.providers.length }) : t('marketDataStatus.checking')}
          </div>
        </div>
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium ${badgeMeta.badgeClass}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${badgeMeta.dotClass}`} />
          {badgeMeta.label}
        </span>
      </div>

      {error && (
        <div className="px-4 pb-3">
          <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400">
            <span className="material-symbols-outlined !text-[14px]">error</span>
            {error}
          </div>
        </div>
      )}

      <div className="px-4 pb-4">
        <div className="border border-slate-200 dark:border-border-dark/70 rounded-lg bg-slate-50/50 dark:bg-surface-dark/50 px-3 py-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">{t('marketDataStatus.lastRefresh')}</div>
              <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{refreshTime}</div>
            </div>
            <div className="text-right text-xs font-medium text-slate-700 dark:text-slate-300">
              {summaryText(t, status)}
            </div>
          </div>
          {detailToggleVisible && (
            <div className="mt-3 border-t border-slate-200 dark:border-border-dark/70 pt-3">
              <button
                type="button"
                onClick={() => setShowDetails(value => !value)}
                className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
              >
                <span className="material-symbols-outlined !text-[14px]">
                  {showDetails ? 'expand_less' : 'expand_more'}
                </span>
                {showDetails ? t('marketDataStatus.hideDetails') : t('marketDataStatus.showDetails')}
              </button>
            </div>
          )}
          {showDetails && lastRefresh && (
            <div className="mt-3 space-y-3">
              {lastRefresh.fx_rates && Object.keys(lastRefresh.fx_rates).length > 0 && (
                <div className="rounded-lg border border-slate-200 dark:border-border-dark/70 bg-white/80 dark:bg-slate-900/30 px-3 py-3">
                  <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">{t('marketDataStatus.fxRatesUsed')}</div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-600 dark:text-slate-300">
                    {Object.entries(lastRefresh.fx_rates).map(([currency, rate]) => (
                      <span
                        key={currency}
                        className="rounded-full bg-slate-100 dark:bg-slate-800 px-2 py-1"
                      >
                        {t('marketDataStatus.fxRateLine', { currency, rate: Number(rate).toFixed(4) })}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {(lastRefresh.refreshed_assets?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-slate-200 dark:border-border-dark/70 bg-white/80 dark:bg-slate-900/30 px-3 py-3">
                  <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">{t('marketDataStatus.latestFetchedPrices')}</div>
                  <div className="mt-2 space-y-2">
                    {lastRefresh.refreshed_assets?.map(asset => (
                      <div
                        key={`${asset.asset_id}-${asset.as_of_date}`}
                        className="flex items-start justify-between gap-3 rounded-md bg-slate-50 dark:bg-slate-800/60 px-2.5 py-2"
                      >
                        <div className="min-w-0">
                          <div className="text-xs font-medium text-slate-800 dark:text-slate-100">{asset.code}</div>
                          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">{asset.asset_id}</div>
                          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                            {t('marketDataStatus.marketVia', { market: marketName(t, asset.market), source: asset.source })}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                            {formatPrice(asset.price)}
                          </div>
                          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">{asset.as_of_date}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(lastRefresh.error_assets?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-red-200 dark:border-red-900/40 bg-red-50/80 dark:bg-red-950/20 px-3 py-3">
                  <div className="text-xs font-semibold text-red-800 dark:text-red-300">{t('marketDataStatus.refreshErrors')}</div>
                  <div className="mt-2 space-y-2">
                    {lastRefresh.error_assets?.map(asset => (
                      <div
                        key={`${asset.asset_id}-${asset.reason}`}
                        className="rounded-md bg-white/80 dark:bg-red-950/20 px-2.5 py-2"
                      >
                        <div className="text-xs font-medium text-slate-800 dark:text-slate-100">{asset.asset_id}</div>
                        <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                          {marketName(t, asset.market)}
                        </div>
                        <div className="text-[11px] text-red-700 dark:text-red-300 mt-1">{asset.reason}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(lastRefresh.skipped_assets?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-amber-200 dark:border-amber-900/40 bg-amber-50/80 dark:bg-amber-950/20 px-3 py-3">
                  <div className="text-xs font-semibold text-amber-800 dark:text-amber-300">{t('marketDataStatus.skippedAssets')}</div>
                  <div className="mt-2 space-y-2">
                    {lastRefresh.skipped_assets?.map(asset => (
                      <div
                        key={`${asset.asset_id}-${asset.reason}`}
                        className="rounded-md bg-white/80 dark:bg-amber-950/20 px-2.5 py-2"
                      >
                        <div className="text-xs font-medium text-slate-800 dark:text-slate-100">{asset.asset_id}</div>
                        <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                          {marketName(t, asset.market)}
                        </div>
                        <div className="text-[11px] text-amber-700 dark:text-amber-300 mt-1">{asset.reason}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="mt-3 space-y-2">
          {status && status.providers.length > 0 ? (
            status.providers.map(provider => (
              <article
                key={`${provider.market}-${provider.fetcher}`}
                className="border border-slate-200 dark:border-border-dark/70 rounded-lg bg-slate-50/50 dark:bg-surface-dark/50 px-3 py-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                      {marketLabel(t, provider)}
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                      {provider.fetcher}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                      {provider.asset_count}
                    </div>
                    <div className="text-[11px] text-slate-500 dark:text-slate-400">{t('marketDataStatus.assets')}</div>
                  </div>
                </div>
              </article>
            ))
          ) : (
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {loading ? t('marketDataStatus.loadingProviders') : t('marketDataStatus.noActiveProviders')}
            </div>
          )}
        </div>

        <button
          onClick={() => { void handleRefresh(); }}
          disabled={refreshing}
          className="mt-4 w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {refreshing ? (
            <span className="material-symbols-outlined !text-[16px] animate-spin">progress_activity</span>
          ) : (
            <span className="material-symbols-outlined !text-[16px]">sync</span>
          )}
          {refreshing ? t('marketDataStatus.refreshingPrices') : t('marketDataStatus.refreshPrices')}
        </button>
      </div>
    </section>
  );
};
