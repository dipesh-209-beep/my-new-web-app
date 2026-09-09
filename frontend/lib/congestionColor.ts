// Piecewise-linear RGB gradient keyed to the backend's actual congestion
// thresholds (_MODERATE_RATIO, _HEAVY_RATIO, score_to_ratio(10) ceiling),
// NOT an arbitrary 0–1 scale.
const STOPS: { ratio: number; r: number; g: number; b: number }[] = [
  { ratio: 1.00, r: 0x22, g: 0xc5, b: 0x5e }, // #22C55E green  — free-flow
  { ratio: 1.15, r: 0xf5, g: 0x9e, b: 0x0b }, // #F59E0B yellow — _MODERATE_RATIO
  { ratio: 1.50, r: 0xf9, g: 0x73, b: 0x16 }, // #F97316 orange — _HEAVY_RATIO
  { ratio: 2.50, r: 0xef, g: 0x44, b: 0x44 }, // #EF4444 red
  { ratio: 4.20, r: 0x7f, g: 0x1d, b: 0x1d }, // #7F1D1D dark red — score_to_ratio(10)
];

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

function toHex(n: number): string {
  return n.toString(16).padStart(2, "0");
}

export function getCongestionColor(ratio: number): string {
  const r = Math.max(1.0, Math.min(4.2, ratio));

  if (r <= STOPS[0].ratio) {
    const s = STOPS[0];
    return `#${toHex(s.r)}${toHex(s.g)}${toHex(s.b)}`;
  }

  for (let i = 0; i < STOPS.length - 1; i++) {
    const a = STOPS[i];
    const b = STOPS[i + 1];
    if (r <= b.ratio) {
      const t = (r - a.ratio) / (b.ratio - a.ratio);
      return `#${toHex(lerp(a.r, b.r, t))}${toHex(lerp(a.g, b.g, t))}${toHex(lerp(a.b, b.b, t))}`;
    }
  }

  const last = STOPS[STOPS.length - 1];
  return `#${toHex(last.r)}${toHex(last.g)}${toHex(last.b)}`;
}
