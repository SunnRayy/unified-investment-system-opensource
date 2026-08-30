import React from 'react';
import { useTranslation } from 'react-i18next';

interface TechnicalDashboardProps {
  signals: Record<string, unknown>;
}

function signalScoreColor(score: number): string {
  if (score < 40) return 'bg-red-500';
  if (score < 60) return 'bg-yellow-400';
  return 'bg-emerald-500';
}

function signalScoreTextColor(score: number): string {
  if (score < 40) return 'text-red-600 dark:text-red-400';
  if (score < 60) return 'text-yellow-600 dark:text-yellow-400';
  return 'text-emerald-600 dark:text-emerald-400';
}

function trendBadgeColor(trend: string): string {
  switch (trend) {
    case 'STRONG_BULL': return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300';
    case 'BULL': return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300';
    case 'NEUTRAL': return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
    case 'BEAR': return 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300';
    case 'STRONG_BEAR': return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300';
    default: return 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400';
  }
}

function rsiColor(rsi: number): string {
  if (rsi < 30) return 'text-emerald-600 dark:text-emerald-400';
  if (rsi > 70) return 'text-red-600 dark:text-red-400';
  return 'text-slate-700 dark:text-slate-300';
}

function genericBadge(value: string | null | undefined): string {
  return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
}

export const TechnicalDashboard: React.FC<TechnicalDashboardProps> = ({ signals }) => {
  const { t } = useTranslation('aiAdvisor');
  const signalScore = typeof signals.signal_score === 'number' ? signals.signal_score : null;
  const trendStatus = typeof signals.trend_status === 'string' ? signals.trend_status : null;
  const maAlignmentScore = typeof signals.ma_alignment_score === 'number' ? signals.ma_alignment_score : null;
  const rsiValue = typeof signals.rsi_value === 'number' ? signals.rsi_value : null;
  const rsiStatus = typeof signals.rsi_status === 'string' ? signals.rsi_status : null;
  const macdStatus = typeof signals.macd_status === 'string' ? signals.macd_status : null;
  const volumeStatus = typeof signals.volume_status === 'string' ? signals.volume_status : null;
  const volumeRatio = typeof signals.volume_ratio === 'number' ? signals.volume_ratio : null;
  const supportLevels = Array.isArray(signals.support_levels) ? (signals.support_levels as number[]) : [];
  const resistanceLevels = Array.isArray(signals.resistance_levels) ? (signals.resistance_levels as number[]) : [];

  return (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4 space-y-4">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('technicalDashboard.title')}</h3>

      {/* Signal Score */}
      {signalScore !== null && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-slate-500 dark:text-slate-400">{t('technicalDashboard.signalScore')}</span>
            <span className={`text-sm font-semibold ${signalScoreTextColor(signalScore)}`}>
              {signalScore.toFixed(0)}/100
            </span>
          </div>
          <div className="w-full bg-slate-100 dark:bg-slate-700 rounded-full h-2">
            <div
              className={`h-2 rounded-full ${signalScoreColor(signalScore)}`}
              style={{ width: `${Math.min(100, Math.max(0, signalScore))}%` }}
            />
          </div>
        </div>
      )}

      {/* Trend Status */}
      {trendStatus && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500 dark:text-slate-400">{t('technicalDashboard.trend')}</span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${trendBadgeColor(trendStatus)}`}>
            {trendStatus.replace('_', ' ')}
          </span>
        </div>
      )}

      {/* MA Alignment */}
      {maAlignmentScore !== null && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500 dark:text-slate-400">{t('technicalDashboard.maAlignment')}</span>
          <span className="text-xs font-medium text-slate-700 dark:text-slate-300">
            {maAlignmentScore === 0 && t('technicalDashboard.maAlignmentNone')}
            {maAlignmentScore === 1 && t('technicalDashboard.maAlignmentPartial')}
            {maAlignmentScore === 2 && t('technicalDashboard.maAlignmentFull')}
          </span>
        </div>
      )}

      {/* RSI */}
      {rsiValue !== null && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500 dark:text-slate-400">RSI</span>
          <div className="flex items-center gap-2">
            <span className={`text-sm font-semibold ${rsiColor(rsiValue)}`}>
              {rsiValue.toFixed(1)}
            </span>
            {rsiStatus && (
              <span className="text-xs text-slate-500 dark:text-slate-400">({rsiStatus})</span>
            )}
            {rsiValue < 30 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                {t('technicalDashboard.oversold')}
              </span>
            )}
            {rsiValue > 70 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300">
                {t('technicalDashboard.overbought')}
              </span>
            )}
          </div>
        </div>
      )}

      {/* MACD */}
      {macdStatus && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500 dark:text-slate-400">MACD</span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${genericBadge(macdStatus)}`}>
            {macdStatus}
          </span>
        </div>
      )}

      {/* Volume */}
      {(volumeStatus || volumeRatio !== null) && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-500 dark:text-slate-400">{t('technicalDashboard.volume')}</span>
          <div className="flex items-center gap-2">
            {volumeStatus && (
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${genericBadge(volumeStatus)}`}>
                {volumeStatus}
              </span>
            )}
            {volumeRatio !== null && (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {t('technicalDashboard.volumeRatio', { ratio: volumeRatio.toFixed(2) })}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Support / Resistance */}
      {(supportLevels.length > 0 || resistanceLevels.length > 0) && (
        <div className="pt-2 border-t border-slate-100 dark:border-slate-700">
          <p className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">{t('technicalDashboard.keyLevels')}</p>
          <div className="grid grid-cols-2 gap-3">
            {resistanceLevels.length > 0 && (
              <div>
                <p className="text-[10px] text-slate-400 mb-1">{t('technicalDashboard.resistance')}</p>
                <div className="space-y-0.5">
                  {resistanceLevels.slice(0, 3).map((lvl, i) => (
                    <div key={i} className="text-xs font-mono text-red-600 dark:text-red-400">
                      {lvl.toLocaleString()}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {supportLevels.length > 0 && (
              <div>
                <p className="text-[10px] text-slate-400 mb-1">{t('technicalDashboard.support')}</p>
                <div className="space-y-0.5">
                  {supportLevels.slice(0, 3).map((lvl, i) => (
                    <div key={i} className="text-xs font-mono text-emerald-600 dark:text-emerald-400">
                      {lvl.toLocaleString()}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
