import React from 'react';
import { useTranslation } from 'react-i18next';

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger';

const TONE_MAP: Record<Tone, { bg: string; fg: string }> = {
  neutral: { bg: 'var(--fill-neutral-soft)', fg: 'var(--color-fg-3)' },
  primary: { bg: 'var(--fill-primary-soft)', fg: 'var(--color-primary)' },
  success: { bg: 'var(--fill-success-soft)', fg: 'var(--zone-green-fg)' },
  warning: { bg: 'var(--fill-warning-soft)', fg: 'var(--zone-yellow-fg)' },
  danger:  { bg: 'var(--fill-danger-soft)',  fg: 'var(--zone-red-fg)' },
};

const STATUS_TONE: Record<string, Tone> = {
  ok:      'success',
  healthy: 'success',
  warning: 'danger',
  review:  'warning',
  high:    'danger',
  low:     'success',
  info:    'primary',
  missing: 'neutral',
};

const STATUS_LABEL_KEY: Record<string, string> = {
  ok:      'status.ok',
  healthy: 'status.healthy',
  warning: 'status.warning',
  review:  'status.review',
  high:    'status.high',
  low:     'status.low',
  info:    'status.info',
  missing: 'status.missing',
};

interface PillProps {
  children?: React.ReactNode;
  tone?: Tone;
  style?: React.CSSProperties;
}

export function Pill({ children, tone = 'neutral', style }: PillProps) {
  const { bg, fg } = TONE_MAP[tone] ?? TONE_MAP.neutral;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 8px', fontSize: 10, fontWeight: 700,
      borderRadius: 4, letterSpacing: '0.04em', textTransform: 'uppercase',
      background: bg, color: fg, fontFamily: 'var(--font-sans)',
      ...style,
    }}>
      {children}
    </span>
  );
}

interface StatusPillProps {
  status: string;
  children?: React.ReactNode;
}

export function StatusPill({ status, children }: StatusPillProps) {
  const { t } = useTranslation('operations');
  const tone = STATUS_TONE[status] ?? 'neutral';
  const key = STATUS_LABEL_KEY[status];
  return <Pill tone={tone}>{children ?? (key ? t(key) : status)}</Pill>;
}
