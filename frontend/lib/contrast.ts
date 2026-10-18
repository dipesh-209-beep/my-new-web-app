// Dependency-free WCAG 2.x contrast math. Used by the tests that guard the
// congestion legend outline (WCAG 1.4.11 non-text contrast) and pin the known
// severity fill values, so a palette edit has to be deliberate.
//
// Only 6-digit `#RRGGBB` is supported, which is all the design tokens use.

function srgbChannelToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function channels(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

/** WCAG relative luminance of a `#RRGGBB` color, in [0, 1]. */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = channels(hex);
  return (
    0.2126 * srgbChannelToLinear(r) +
    0.7152 * srgbChannelToLinear(g) +
    0.0722 * srgbChannelToLinear(b)
  );
}

/** WCAG contrast ratio between two `#RRGGBB` colors, in [1, 21]. Symmetric. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const lighter = Math.max(la, lb);
  const darker = Math.min(la, lb);
  return (lighter + 0.05) / (darker + 0.05);
}
