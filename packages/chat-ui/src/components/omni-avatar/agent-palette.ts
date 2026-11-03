import { MARK_PALETTE, type Palette } from './planet';

function hex(h: number, s: number, l: number) {
  // HSL (degrees, 0..1, 0..1) to #rrggbb, as the planet mixes colours in hex.
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    const c = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(c * 255)
      .toString(16)
      .padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** A stable hue for a name: the same agent always looks the same. */
export function agentHue(seed: string) {
  let hash = 2166136261;
  for (const ch of seed) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619);
  return (hash >>> 0) % 360;
}

/** The mark's palette turned to an agent's own hue; the warm glow stays. */
export function agentPalette(seed: string): Palette {
  const h = agentHue(seed);
  const deep = (h + 40) % 360;
  return {
    light: hex(h, 0.95, 0.83),
    mid: hex(h, 0.9, 0.66),
    deep: hex(deep, 0.55, 0.46),
    ringA: hex(h, 0.95, 0.78),
    ringB: hex(deep, 0.8, 0.68),
    energy: hex(h, 0.95, 0.81),
    warm: MARK_PALETTE.warm,
  };
}
