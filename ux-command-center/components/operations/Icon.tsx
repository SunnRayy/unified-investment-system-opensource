import React from 'react';

interface IconProps {
  name: string;
  size?: number;
  fill?: boolean;
  color?: string;
  style?: React.CSSProperties;
}

export function Icon({ name, size = 18, fill = false, color, style }: IconProps) {
  return (
    <span
      className={'material-symbols-outlined' + (fill ? ' filled-icon' : '')}
      style={{ fontSize: size, color: color ?? 'inherit', lineHeight: 1, ...style }}
    >
      {name}
    </span>
  );
}
