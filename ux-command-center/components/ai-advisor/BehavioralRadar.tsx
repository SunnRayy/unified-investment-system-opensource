import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
} from 'recharts';
import type { BehavioralMetric } from '../../src/services/api';
import type { TFunction } from 'i18next';

interface BehavioralRadarProps {
  metrics: BehavioralMetric[];
  onCompute: () => void;
  computing: boolean;
}

function dimensionLabels(t: TFunction<'aiAdvisor'>): Record<string, string> {
  return {
    contrarian_tendency: t('behavioralRadar.dimensions.contrarianTendency'),
    position_sizing_discipline: t('behavioralRadar.dimensions.positionSizingDiscipline'),
    decision_speed: t('behavioralRadar.dimensions.decisionSpeed'),
    loss_tolerance: t('behavioralRadar.dimensions.lossTolerance'),
    strategy_compliance: t('behavioralRadar.dimensions.strategyCompliance'),
    rebalance_discipline: t('behavioralRadar.dimensions.rebalanceDiscipline'),
    // F5 (PRD 2026-07-07): contrarian decomposition by order origin. Systematic
    // is discipline (higher = better); Manual is the watched behavioral-weakness
    // metric — pinned at a neutral 0.5 score by the backend so radar geometry
    // never rewards it; the alert lives in metadata, not the shape.
    systematic_contrarian: t('behavioralRadar.dimensions.systematicContrarian'),
    manual_contrarian: t('behavioralRadar.dimensions.manualContrarian'),
  };
}

export const BehavioralRadar: React.FC<BehavioralRadarProps> = ({ metrics, onCompute, computing }) => {
  const { t } = useTranslation('aiAdvisor');
  const DIMENSION_LABELS = dimensionLabels(t);
  const radarData = metrics.map((m) => ({
    subject: DIMENSION_LABELS[m.dimension] || m.dimension,
    score: Math.round(m.score * 100),
    fullMark: 100,
  }));

  return (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('behavioralRadar.title')}</h3>
        <button
          type="button"
          onClick={onCompute}
          disabled={computing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {computing ? (
            <>
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              {t('behavioralRadar.computing')}
            </>
          ) : (
            t('behavioralRadar.recompute')
          )}
        </button>
      </div>

      {metrics.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <span className="material-symbols-outlined !text-[36px] text-slate-300 dark:text-slate-600 mb-3">
            radar
          </span>
          <p className="text-sm text-slate-400">{t('behavioralRadar.emptyHint')}</p>
        </div>
      ) : (
        <>
          {/* Radar Chart */}
          <div className="h-64 w-full mb-4">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} margin={{ top: 10, right: 30, bottom: 10, left: 30 }}>
                <PolarGrid stroke="#e2e8f0" />
                <PolarAngleAxis
                  dataKey="subject"
                  tick={{ fontSize: 11, fill: '#94a3b8' }}
                />
                <Radar
                  name={t('behavioralRadar.score')}
                  dataKey="score"
                  stroke="#6366f1"
                  fill="#6366f1"
                  fillOpacity={0.25}
                  strokeWidth={2}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          {/* Detail rows */}
          <div className="space-y-0.5">
            {metrics.map((m) => (
              <div key={m.dimension} className="flex items-center gap-3 py-1.5">
                <span className="text-xs text-slate-500 w-24 shrink-0">
                  {DIMENSION_LABELS[m.dimension] || m.dimension}
                </span>
                <div className="flex-1 h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      m.score >= 0.7
                        ? 'bg-emerald-400'
                        : m.score >= 0.4
                        ? 'bg-amber-400'
                        : 'bg-red-400'
                    }`}
                    style={{ width: `${m.score * 100}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-slate-600 dark:text-slate-400 w-28 shrink-0 text-right">
                  {m.label}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
};
