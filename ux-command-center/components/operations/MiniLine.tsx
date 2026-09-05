import React from 'react';

interface DataPoint {
  m?: string;
  [key: string]: unknown;
}

interface MiniLineProps {
  data: DataPoint[];
  width?: number;
  height?: number;
  accessor?: string;
  targetAccessor?: string | null;
}

export function MiniLine({ data, width = 480, height = 140, accessor = 'v', targetAccessor = null }: MiniLineProps) {
  if (!data || data.length < 2) return null;

  const pad = { l: 12, r: 12, t: 8, b: 22 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const vals = data.flatMap((d) => {
    const points: number[] = [d[accessor] as number];
    if (targetAccessor && d[targetAccessor] != null) points.push(d[targetAccessor] as number);
    return points;
  }).filter((v) => v != null);
  const minV = Math.min(...vals) * 0.95;
  const maxV = Math.max(...vals) * 1.05;
  const range = maxV - minV || 1;
  const xStep = w / (data.length - 1);

  const xOf = (i: number) => pad.l + i * xStep;
  const yOf = (v: number) => pad.t + h - ((v - minV) / range) * h;

  const linePath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(d[accessor] as number)}`).join(' ');
  const areaPath = `${linePath} L${xOf(data.length - 1)},${pad.t + h} L${pad.l},${pad.t + h} Z`;
  const targetPath = targetAccessor
    ? data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xOf(i)},${yOf(d[targetAccessor] as number)}`).join(' ')
    : null;

  const last = data[data.length - 1];

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <defs>
        <linearGradient id="ops-miniline-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#135bec" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#135bec" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#ops-miniline-grad)" />
      {targetPath && <path d={targetPath} fill="none" stroke="var(--color-fg-3)" strokeWidth="1.2" strokeDasharray="3 3" />}
      <path d={linePath} fill="none" stroke="#135bec" strokeWidth="1.8" />
      {data.map((d, i) => {
        if (i % 2 !== 0 && i !== data.length - 1) return null;
        return (
          <text key={i} x={xOf(i)} y={height - 6} fontSize="9"
                fill="var(--color-fg-4)" textAnchor="middle" fontFamily="var(--font-mono)">
            {d.m}
          </text>
        );
      })}
      <circle cx={xOf(data.length - 1)} cy={yOf(last[accessor] as number)}
              r="3" fill="#135bec" stroke="var(--color-card)" strokeWidth="1.5" />
    </svg>
  );
}
