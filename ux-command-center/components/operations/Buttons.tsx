import React from 'react';
import { Icon } from './Icon';

interface ChipBtnProps {
  children?: React.ReactNode;
  icon?: string;
  onClick?: () => void;
  primary?: boolean;
  style?: React.CSSProperties;
  disabled?: boolean;
}

export function ChipBtn({ children, icon, onClick, primary, style, disabled }: ChipBtnProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="ds-chip-btn"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '6px 12px', fontSize: 11,
        fontFamily: primary ? 'var(--font-sans)' : 'var(--font-mono)',
        fontWeight: primary ? 700 : 500,
        letterSpacing: primary ? '0.02em' : '0',
        borderRadius: 8,
        border: primary ? 'none' : '1px solid var(--color-border)',
        background: primary ? 'var(--color-primary)' : 'var(--color-card)',
        color: primary ? '#fff' : 'var(--color-fg-2)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background 120ms ease',
        boxShadow: primary ? 'var(--shadow-primary-glow)' : 'none',
        ...style,
      }}
    >
      {icon && <Icon name={icon} size={14} />}
      {children}
    </button>
  );
}

interface ActionBtnProps {
  children?: React.ReactNode;
  icon?: string;
  variant?: 'primary' | 'secondary';
  onClick?: () => void;
  disabled?: boolean;
  danger?: boolean;
}

export function ActionBtn({ children, icon, variant = 'secondary', onClick, disabled, danger }: ActionBtnProps) {
  const bg = variant === 'primary' ? 'var(--color-primary)'
           : danger ? 'color-mix(in srgb, var(--color-danger) 10%, transparent)'
           : 'var(--color-card)';
  const fg = variant === 'primary' ? '#fff'
           : danger ? 'var(--color-danger)'
           : 'var(--color-fg-2)';
  const border = variant === 'primary' ? 'none'
               : danger ? '1px solid color-mix(in srgb, var(--color-danger) 30%, transparent)'
               : '1px solid var(--color-border)';

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="ds-chip-btn"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '6px 12px', fontSize: 11,
        fontFamily: variant === 'primary' ? 'var(--font-sans)' : 'var(--font-mono)',
        fontWeight: variant === 'primary' ? 700 : 500,
        letterSpacing: variant === 'primary' ? '0.02em' : '0',
        borderRadius: 8, border, background: bg, color: fg,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        boxShadow: variant === 'primary' ? 'var(--shadow-primary-glow)' : 'none',
      }}
    >
      {icon && <Icon name={icon} size={14} />}
      {children}
    </button>
  );
}

interface IconBtnProps {
  icon: string;
  title?: string;
  danger?: boolean;
  onClick?: () => void;
}

export function IconBtn({ icon, title, danger, onClick }: IconBtnProps) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        border: danger
          ? '1px solid color-mix(in srgb, var(--color-danger) 25%, transparent)'
          : '1px solid var(--color-border)',
        background: 'var(--color-card)',
        color: danger ? 'var(--color-danger)' : 'var(--color-fg-3)',
        borderRadius: 6, width: 26, height: 26,
        cursor: 'pointer', display: 'inline-grid', placeItems: 'center',
      }}
    >
      <Icon name={icon} size={13} />
    </button>
  );
}
