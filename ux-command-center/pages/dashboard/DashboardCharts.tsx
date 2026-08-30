/**
 * Dashboard Charts — SVG Sparkline, Donut, and DriftBar primitives
 * Following Huinsight Design System: Inter + Roboto Mono, slate palette, semantic colors
 */
import React from 'react';

/* ---------- Sparkline ---------- */
export const Sparkline: React.FC<{
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
}> = ({ data, width = 120, height = 36, color = 'var(--color-primary)', fill = true }) => {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const points = data.map((v, i) => [i * stepX, height - ((v - min) / span) * (height - 4) - 2]);
  const d = points.map((p, i) => (i === 0 ? `M${p[0]},${p[1]}` : `L${p[0]},${p[1]}`)).join(' ');
  const fillD = `${d} L${width},${height} L0,${height} Z`;
  const last = points[points.length - 1];
  const gradId = `spark-${Math.random().toString(36).slice(2, 8)}`;
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {fill && (
        <>
          <defs>
            <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.18" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={fillD} fill={`url(#${gradId})`} />
        </>
      )}
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill={color} />
    </svg>
  );
};

/* ---------- Donut ---------- */
export const Donut: React.FC<{
  data: Array<{ cur: number; color: string; name: string }>;
  size?: number;
  thickness?: number;
}> = ({ data, size = 180, thickness = 22 }) => {
  const total = data.reduce((s, d) => s + d.cur, 0);
  if (total === 0) return null;
  const r = size / 2 - 2;
  const inner = r - thickness;
  const cx = size / 2, cy = size / 2;
  let acc = 0;
  const slices = data.map((d) => {
    const a0 = (acc / total) * Math.PI * 2 - Math.PI / 2;
    acc += d.cur;
    const a1 = (acc / total) * Math.PI * 2 - Math.PI / 2;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const xi1 = cx + inner * Math.cos(a1), yi1 = cy + inner * Math.sin(a1);
    const xi0 = cx + inner * Math.cos(a0), yi0 = cy + inner * Math.sin(a0);
    const path = [
      `M${x0},${y0}`,
      `A${r},${r} 0 ${large} 1 ${x1},${y1}`,
      `L${xi1},${yi1}`,
      `A${inner},${inner} 0 ${large} 0 ${xi0},${yi0}`,
      'Z',
    ].join(' ');
    return { path, color: d.color, name: d.name };
  });
  return (
    <svg width={size} height={size} style={{ display: 'block' }}>
      {slices.map((s, i) => (
        <path key={i} d={s.path} fill={s.color} />
      ))}
    </svg>
  );
};

/* ---------- Drift bar ---------- */
export const DriftBar: React.FC<{
  pct: number;
  target: number;
  color: string;
}> = ({ pct, target, color }) => {
  const max = Math.max(pct, target) * 1.15 + 2;
  const w = 120;
  const cur = (pct / max) * w;
  const tgt = (target / max) * w;
  return (
    <svg width={w} height={14} style={{ display: 'block' }}>
      <rect x="0" y="6" width={w} height="2" fill="var(--color-border-soft)" rx="1" />
      <rect x="0" y="5" width={cur} height="4" fill={color} rx="1" />
      <line x1={tgt} x2={tgt} y1="2" y2="12" stroke="var(--color-fg-2)" strokeWidth="1.5" />
    </svg>
  );
};

/* ---------- Area Chart ---------- */
export const AreaChart: React.FC<{
  data: Array<{ m: string; v: number }>;
  width?: number;
  height?: number;
  color?: string;
}> = ({ data, width = 780, height = 320, color = '#135bec' }) => {
  if (!data || data.length < 2) return null;
  const padL = 56, padR = 12, padT = 12, padB = 28;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const min = Math.min(...data.map((d) => d.v));
  const max = Math.max(...data.map((d) => d.v));
  const yMin = Math.max(0, min - (max - min) * 0.15);
  const yMax = max + (max - min) * 0.05;
  const span = yMax - yMin || 1;
  const xAt = (i: number) => padL + (i / (data.length - 1)) * w;
  const yAt = (v: number) => padT + h - ((v - yMin) / span) * h;
  const linePts = data.map((d, i) => [xAt(i), yAt(d.v)]);
  const lineD = linePts.map((p, i) => (i === 0 ? `M${p[0]},${p[1]}` : `L${p[0]},${p[1]}`)).join(' ');
  const fillD = `${lineD} L${padL + w},${padT + h} L${padL},${padT + h} Z`;
  const yTicks = 4;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => yMin + (span * i) / yTicks);
  const xTickStride = Math.ceil(data.length / 5);
  const xTicks = data.map((d, i) => ({ i, m: d.m })).filter((d) => d.i % xTickStride === 0 || d.i === data.length - 1);
  const gradId = `area-grad-${Math.random().toString(36).slice(2, 8)}`;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ display: 'block' }}>
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.30" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {yTickValues.map((v, i) => (
        <g key={i}>
          <line x1={padL} x2={padL + w} y1={yAt(v)} y2={yAt(v)} stroke="var(--color-border-soft)" strokeDasharray={i === 0 ? '0' : '2 3'} />
          <text className="money-value" x={padL - 8} y={yAt(v)} textAnchor="end" dominantBaseline="middle" fontSize="10" fontFamily="var(--font-mono)" fill="var(--color-fg-4)">
            ¥{(v / 1_000_000).toFixed(2)}M
          </text>
        </g>
      ))}
      <path d={fillD} fill={`url(#${gradId})`} />
      <path d={lineD} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {xTicks.map((t) => (
        <text key={t.i} x={xAt(t.i)} y={padT + h + 16} textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)" fill="var(--color-fg-4)">
          {t.m}
        </text>
      ))}
      <circle cx={linePts[linePts.length - 1][0]} cy={linePts[linePts.length - 1][1]} r="4" fill={color} />
      <circle cx={linePts[linePts.length - 1][0]} cy={linePts[linePts.length - 1][1]} r="8" fill={color} opacity="0.18" />
    </svg>
  );
};
