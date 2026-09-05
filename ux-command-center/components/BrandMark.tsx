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
 *
 * The outline is two quadratic Béziers, not the elliptical arcs it started as.
 * Those arcs read `A72,32 ... A72,20` — an eye 32 units tall above the midline
 * and 20 below — and drew nothing of the sort. An SVG arc only reaches its `ry`
 * if `rx` is close to the chord's half-width, and here `rx` was 72 against a
 * half-chord of 42, so the curve flattened out long before it could rise. The
 * outline came out 9.8 units tall against 84 wide: a horizontal sliver with a
 * 38-unit iris hanging out of it, top and bottom. At 24px in the sidebar you
 * read the iris and skip the outline, which is why it survived; at 48px on the
 * login screen it is the first thing you see.
 *
 * The Béziers restore the proportions those numbers were asking for — apex at
 * y=18, base at y=70 — with control points that say what they mean instead of
 * being solved for. Changing the outline means re-exporting the PNG icon set;
 * `docs/marketing/huinsighticonmark.svg` is the original delivery and is left
 * as received.
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
      d="M8,50 Q50,-14 92,50 Q50,90 8,50 Z"
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
