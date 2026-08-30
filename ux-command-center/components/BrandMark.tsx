import React from 'react';

/**
 * The Huinsight mark — 慧眼, the discerning eye.
 *
 * An almond eye whose iris is cut into six even facets. The dual reading is
 * intended: a marquise-cut diamond and a stylised eye share a silhouette, so
 * the mark lands as both discernment and wealth depending on what the viewer
 * notices first. It is not explained anywhere in the product; people see it or
 * they don't.
 *
 * Colour comes from context rather than being baked in:
 *   - the outline and iris use `currentColor`, so the mark inherits text colour
 *   - the facets use `--brand-spark`, the gold accent
 *   - the pupil highlight uses `--brand-mark-hole`, which must match whatever
 *     surface the mark sits on, since it reads as a gap punched through the
 *     iris rather than as a white dot
 *
 * Detail budget: this version is for 48px and up. Below that the facets blur
 * into noise, which is why the favicons ship a separate simplified glyph
 * instead of scaling this one down.
 */
export const BrandMark: React.FC<{ className?: string; title?: string }> = ({
  className,
  title,
}) => (
  <svg
    className={className}
    viewBox="0 0 100 100"
    xmlns="http://www.w3.org/2000/svg"
    role={title ? 'img' : undefined}
    aria-label={title}
    aria-hidden={title ? undefined : true}
    focusable="false"
  >
    {title ? <title>{title}</title> : null}
    <path
      d="M8,50 A72,32 0 0 1 92,50 A72,20 0 0 1 8,50 Z"
      fill="none"
      stroke="currentColor"
      strokeWidth="4.5"
      strokeLinejoin="round"
    />
    <circle cx="50" cy="50" r="19" fill="currentColor" />
    <g
      stroke="var(--brand-spark, #dd9a2e)"
      strokeWidth="2.6"
      strokeLinecap="round"
    >
      <line x1="50" y1="43" x2="50" y2="31" />
      <line x1="56.06" y1="46.5" x2="66.45" y2="40.5" />
      <line x1="56.06" y1="53.5" x2="66.45" y2="59.5" />
      <line x1="50" y1="57" x2="50" y2="69" />
      <line x1="43.94" y1="53.5" x2="33.55" y2="59.5" />
      <line x1="43.94" y1="46.5" x2="33.55" y2="40.5" />
    </g>
    <circle cx="50" cy="50" r="5.5" fill="var(--brand-mark-hole, #fff)" />
    <path
      d="M1,50 L9,50 M91,50 L99,50"
      stroke="currentColor"
      strokeWidth="4.5"
      strokeLinecap="round"
      opacity="0.55"
    />
  </svg>
);
