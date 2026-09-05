import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { api, RiskMetrics, RiskCorrelation, ExportAPI } from '../src/services/api';

export const RiskMatrix: React.FC = () => {
  const { t } = useTranslation('reports');
  const { includeNonRebalanceable } = usePortfolioFilter();
  const [metrics, setMetrics] = useState<RiskMetrics | null>(null);
  const [correlation, setCorrelation] = useState<RiskCorrelation | null>(null);
  const [corrLevel, setCorrLevel] = useState<'top' | 'sub'>('top');
  const [error, setError] = useState<string | null>(null);
  // Three states, not two. "Still fetching", "fetched and there is nothing to
  // show" and "the fetch failed" used to collapse into one spinner that never
  // stopped, because the only test was `correlation.assets.length > 0`.
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, [corrLevel, includeNonRebalanceable]);

  const loadData = async () => {
    setError(null);
    setLoading(true);
    try {
      const [metricsData, correlationData] = await Promise.all([
        api.getRiskMetrics(includeNonRebalanceable),
        api.getRiskCorrelation(corrLevel, includeNonRebalanceable)
      ]);
      setMetrics(metricsData);
      setCorrelation(correlationData);
    } catch (e) {
      console.error(e);
      // Leave metrics/correlation null. Every tile renders "--" when it has no
      // data; that is the honest reading and the banner below says why.
      setMetrics(null);
      setCorrelation(null);
      setError(t('riskMatrix.errors.load'));
    } finally {
      setLoading(false);
    }
  };

  const getStatusStyle = (status: string | undefined) => {
    switch (status?.toUpperCase()) {
      case 'LOW':
        return 'bg-green-100 text-green-600';
      case 'MED':
        return 'bg-amber-100 text-amber-600';
      case 'HIGH':
        return 'bg-rose-100 text-rose-600';
      case 'GOOD':
      case 'EXCELLENT':
        return 'bg-purple-100 text-purple-600';
      case 'AVG':
        return 'bg-slate-100 text-slate-500';
      case 'POOR':
        return 'bg-rose-100 text-rose-600';
      default:
        return 'bg-slate-100 text-slate-500';
    }
  };

  const getCellStyle = (value: number | null) => {
    if (value === null) return 'bg-slate-50 text-slate-300 dark:bg-slate-800 dark:text-slate-500';
    if (value === 1) return 'bg-slate-100 dark:bg-surface-dark';
    if (value > 0.5) return 'bg-rose-50 text-rose-600';
    if (value < -0.2) return 'bg-green-50 text-green-600';
    return 'bg-blue-50 text-blue-600';
  };

  const insufficientPairs = correlation?.insufficient_pairs ?? 0;
  const totalPairs = correlation?.total_pairs ?? 0;
  const showCoverageWarning = totalPairs > 0 && (insufficientPairs / totalPairs) > 0.5;
  const minOverlapPeriods = correlation?.min_overlap_periods ?? 8;
  const jumpExclusions = correlation?.excluded_jump_points_count ?? 0;
  const winsorLow = correlation?.winsor_p_low ?? 0.05;
  const winsorHigh = correlation?.winsor_p_high ?? 0.95;

  return (
    <div data-testid="risk-page" className="p-8 space-y-8 bg-gray-50 dark:bg-background-dark min-h-screen">
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
          <p className="text-red-700 text-sm">{error}</p>
        </div>
      )}
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <span className="hover:text-primary transition-colors cursor-pointer">{t('riskMatrix.breadcrumbDashboard')}</span>
          <span className="material-symbols-outlined !text-xs">chevron_right</span>
          <span className="text-slate-600 dark:text-slate-300 font-medium">{t('riskMatrix.breadcrumbRiskMatrix')}</span>
        </div>
        <div className="flex flex-wrap justify-between items-end gap-4">
          <div className="space-y-1">
            <h1 className="text-3xl lg:text-4xl font-black tracking-tight text-slate-900 dark:text-white">{t('riskMatrix.title')}</h1>
            <p className="text-slate-500 dark:text-slate-400 text-sm flex items-center gap-2">
              <span className="material-symbols-outlined !text-base text-emerald-500">update</span>
              {t('riskMatrix.lastCalculation', { time: new Date().toLocaleTimeString() })}
              {correlation?.method && <span className="text-xs bg-slate-100 dark:bg-surface-dark px-2 py-0.5 rounded ml-2">{t('riskMatrix.method', { method: correlation.method })}</span>}
            </p>
          </div>
          <div className="flex gap-3">
            <button disabled title={t('riskMatrix.comingSoon')} className="flex items-center gap-2 px-4 py-2 bg-white dark:bg-surface-dark border border-slate-200 dark:border-border-dark rounded-lg text-sm font-bold text-slate-400 opacity-50 cursor-not-allowed">
              <span className="material-symbols-outlined !text-[18px]">share</span>
              {t('riskMatrix.share')}
            </button>
            <button onClick={() => ExportAPI.downloadAiContext()} className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg text-sm font-bold shadow-lg shadow-primary/20 hover:bg-blue-600 transition-colors">
              <span className="material-symbols-outlined !text-[18px]">file_download</span>
              {t('riskMatrix.exportAiContext')}
            </button>
          </div>
        </div>
      </div>

      {/* Stats Overview */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm">
          <div className="flex justify-between items-start mb-4">
            <div className="bg-blue-50 dark:bg-blue-900/20 p-3 rounded-xl">
              <span className="material-symbols-outlined text-blue-500">show_chart</span>
            </div>
            <span className={`text-xs font-bold px-2 py-1 rounded ${getStatusStyle(metrics?.volatility_status)}`}>
              {metrics?.volatility_status || t('riskMatrix.notAvailable')}
            </span>
          </div>
          <p className="text-slate-500 dark:text-slate-400 text-xs font-bold uppercase tracking-widest">{t('riskMatrix.portfolioVolatility')}</p>
          <h3 className="text-3xl font-black text-slate-900 dark:text-white mt-1">{metrics ? metrics.volatility : '--'}%</h3>
          <p className="text-xs text-slate-400 mt-2">{t('riskMatrix.annualizedStdDev')}</p>
        </div>

        <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm">
          <div className="flex justify-between items-start mb-4">
            <div className="bg-amber-50 dark:bg-amber-900/20 p-3 rounded-xl">
              <span className="material-symbols-outlined text-amber-500">balance</span>
            </div>
            <span className="text-xs font-bold bg-slate-100 text-slate-500 px-2 py-1 rounded">
              {metrics && metrics.div_score >= 7 ? t('riskMatrix.diversification.good') : metrics && metrics.div_score >= 4 ? t('riskMatrix.diversification.avg') : t('riskMatrix.diversification.low')}
            </span>
          </div>
          <p className="text-slate-500 dark:text-slate-400 text-xs font-bold uppercase tracking-widest">{t('riskMatrix.diversificationScore')}</p>
          <h3 className="text-3xl font-black text-slate-900 dark:text-white mt-1">{metrics ? metrics.div_score : '--'}/10</h3>
          <p className="text-xs text-slate-400 mt-2">{t('riskMatrix.basedOnCorrelation')}</p>
        </div>

        <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm relative overflow-hidden">
          <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-br from-purple-500/10 to-transparent rounded-bl-full"></div>
          <div className="flex justify-between items-start mb-4">
            <div className="bg-purple-50 dark:bg-purple-900/20 p-3 rounded-xl">
              <span className="material-symbols-outlined text-purple-500">trending_up</span>
            </div>
            <span className={`text-xs font-bold px-2 py-1 rounded ${getStatusStyle(metrics?.sharpe_status)}`}>
              {metrics?.sharpe_status || t('riskMatrix.notAvailable')}
            </span>
          </div>
          <p className="text-slate-500 dark:text-slate-400 text-xs font-bold uppercase tracking-widest">{t('riskMatrix.sharpeRatio')}</p>
          <h3 className="text-3xl font-black text-slate-900 dark:text-white mt-1">{metrics ? metrics.sharpe : '--'}</h3>
          <p className="text-xs text-slate-400 mt-2">{t('riskMatrix.riskAdjustedReturn')}</p>
        </div>

        <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm">
          <div className="flex justify-between items-start mb-4">
            <div className="bg-rose-50 dark:bg-rose-900/20 p-3 rounded-xl">
              <span className="material-symbols-outlined text-rose-500">warning</span>
            </div>
            <span className={`text-xs font-bold px-2 py-1 rounded ${getStatusStyle(metrics?.var_95_status)}`}>
              {metrics?.var_95_status || t('riskMatrix.risk')}
            </span>
          </div>
          <p className="text-slate-500 dark:text-slate-400 text-xs font-bold uppercase tracking-widest">{t('riskMatrix.valueAtRisk')}</p>
          <h3 className="text-3xl font-black text-slate-900 dark:text-white mt-1">{metrics ? metrics.var_95 : '--'}%</h3>
          <p className="text-xs text-slate-400 mt-2">{t('riskMatrix.dailyLossEstimate')}</p>
        </div>
      </div>

      {/* Correlation Matrix & Beta */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white dark:bg-card-dark p-8 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm">
          <div className="flex justify-between items-center mb-6">
            <h3 className="font-bold text-lg flex items-center gap-2">
              <span className="material-symbols-outlined text-slate-400">grid_on</span>
              {t('riskMatrix.correlationMatrix')}
              {correlation?.method && <span className="text-xs bg-slate-100 dark:bg-surface-dark px-2 py-0.5 rounded ml-2">{correlation.method}</span>}
            </h3>
            <div className="flex bg-slate-100 dark:bg-surface-dark p-1 rounded-lg">
              <button
                onClick={() => setCorrLevel('top')}
                className={`px-3 py-1.5 text-xs font-bold rounded-md transition-all ${corrLevel === 'top' ? 'bg-white shadow-sm text-primary dark:bg-card-dark' : 'text-slate-500 hover:text-slate-700'}`}
              >
                {t('riskMatrix.topClass')}
              </button>
              <button
                onClick={() => setCorrLevel('sub')}
                className={`px-3 py-1.5 text-xs font-bold rounded-md transition-all ${corrLevel === 'sub' ? 'bg-white shadow-sm text-primary dark:bg-card-dark' : 'text-slate-500 hover:text-slate-700'}`}
              >
                {t('riskMatrix.subClass')}
              </button>
            </div>
          </div>

          {correlation && correlation.assets.length > 0 ? (
            <div>
              <div className="overflow-x-auto">
                <div className={`grid gap-2 text-xs font-mono`} style={{ gridTemplateColumns: `1fr repeat(${correlation.assets.length}, 1fr)` }}>
                  {/* Header row */}
                  <div className="col-span-1"></div>
                  {correlation.assets.map(asset => (
                    <div key={asset} className="text-center font-bold text-slate-500 truncate px-1" title={asset}>
                      {asset.length > 10 ? asset.substring(0, 10) + '...' : asset}
                    </div>
                  ))}

                  {/* Data rows */}
                  {correlation.matrix.map(row => (
                    <React.Fragment key={row.asset}>
                      <div className="font-bold text-slate-500 flex items-center justify-end pr-4 truncate min-w-0" title={row.asset}>
                        <span className="truncate">{row.asset}</span>
                      </div>
                      {correlation.assets.map(colAsset => {
                        const cell = row.correlations[colAsset] ?? null;
                        const value = cell?.value ?? null;
                        const isLowConfidence = Boolean(cell?.low_confidence && value !== null);
                        return (
                          <div
                            key={colAsset}
                            className={`p-3 rounded flex items-center justify-center font-bold ${getCellStyle(value)}`}
                          >
                            {value === null ? '–' : value.toFixed(2)}
                            {isLowConfidence && (
                              <span className="ml-1 rounded bg-amber-100 px-1 text-[9px] font-bold text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                                {t('riskMatrix.lowConfidence')}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </React.Fragment>
                  ))}
                </div>
              </div>
              {showCoverageWarning && (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300">
                  {t('riskMatrix.coverageWarning')}
                </div>
              )}
              <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-border-dark dark:bg-slate-900/40 dark:text-slate-300">
                {t('riskMatrix.windowSummary', {
                  start: correlation.window_start || t('riskMatrix.notAvailable'),
                  end: correlation.window_end || t('riskMatrix.notAvailable'),
                  minOverlap: minOverlapPeriods,
                  jumpExclusions,
                  winsorLow: (winsorLow * 100).toFixed(0),
                  winsorHigh: (winsorHigh * 100).toFixed(0),
                })}
              </div>
              <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
                {t('riskMatrix.correlationLegend')}
              </p>
            </div>
          ) : loading ? (
            <div className="text-center text-slate-400 py-8">
              {t('riskMatrix.loadingMatrix')}
            </div>
          ) : error ? (
            <div className="text-center text-slate-400 py-8">
              {t('riskMatrix.matrixUnavailable')}
            </div>
          ) : (
            <div className="text-center text-slate-400 py-8 space-y-1">
              <p>{t('riskMatrix.matrixInsufficientHistory')}</p>
              <p className="text-xs text-slate-400">
                {t('riskMatrix.matrixInsufficientHistoryHint', { minOverlap: minOverlapPeriods })}
              </p>
            </div>
          )}
        </div>

        <div className="space-y-6">
          <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm">
            <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1">{t('riskMatrix.portfolioBeta')}</p>
            <div className="flex items-end gap-3">
              <h2 className="text-4xl font-black text-slate-900 dark:text-white">{metrics ? metrics.beta : '--'}</h2>
              <span className="text-xs font-bold text-slate-400 mb-1">{t('riskMatrix.vsSp500')}</span>
            </div>
            <div className="mt-4 w-full bg-slate-100 dark:bg-surface-dark h-2 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary rounded-full relative"
                style={{ width: `${Math.min(100, (metrics?.beta || 0) * 50)}%` }}
              ></div>
            </div>
            <p className="text-xs text-slate-500 mt-2">
              {metrics?.beta && metrics.beta < 0.8 ? t('riskMatrix.betaLower') :
                metrics?.beta && metrics.beta > 1.2 ? t('riskMatrix.betaHigher') :
                  t('riskMatrix.betaModerate')}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
