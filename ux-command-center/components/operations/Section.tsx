import React from 'react';
import { Icon } from './Icon';

interface SectionTitleProps {
  icon?: string;
  title: string;
  right?: React.ReactNode;
  style?: React.CSSProperties;
}

export function SectionTitle({ icon, title, right, style }: SectionTitleProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, gap: 12, ...style }}>
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        {icon && <Icon name={icon} size={16} color="var(--color-fg-3)" />}
        <h3 className="uis-h3" style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{title}</h3>
      </div>
      {right}
    </div>
  );
}

interface SectionProps {
  title?: string;
  eyebrow?: string;
  icon?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function Section({ title, eyebrow, icon, right, children, style }: SectionProps) {
  return (
    <div style={style}>
      {(title || eyebrow) && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 12 }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            {icon && <Icon name={icon} size={16} color="var(--color-fg-3)" />}
            {eyebrow && <span className="uis-eyebrow" style={{ fontSize: 10 }}>{eyebrow}</span>}
            {title && <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--color-fg-1)' }}>{title}</h3>}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}
