/**
 * Omarchy's `colors.toml` palette, resolved the way Omarchy resolves it.
 *
 * A theme file declares some subset of semantic keys (`background`,
 * `foreground`, `accent`, `muted`, `selection`, the six ANSI hues and
 * their `bright_*` partners, shaded backgrounds and foregrounds) and
 * possibly the legacy `color0`..`color15` names. Omarchy's
 * `omarchy-theme-color` script fills the gaps with a fixed cascade of
 * aliases, ANSI fallbacks and derived shades; every consumer of a theme
 * (terminal OSC sequences, tmux, generated app configs) goes through
 * that one resolver so they all agree. This module is that cascade in
 * TypeScript, so any theme Omarchy accepts renders here as Omarchy
 * would render it.
 */

export type RawPalette = Record<string, string>;

/** Every key the resolver guarantees, as a hex string (`mode` aside). */
export type Palette = {
  mode: "dark" | "light";
  accent: string;
  selection: string;
  selection_background: string;
  selection_foreground: string;
  muted: string;
  cursor: string;
  background: string;
  dark_background: string;
  darker_background: string;
  lighter_background: string;
  foreground: string;
  dark_foreground: string;
  light_foreground: string;
  bright_foreground: string;
  red: string;
  yellow: string;
  orange: string;
  green: string;
  cyan: string;
  blue: string;
  magenta: string;
  brown: string;
  bright_red: string;
  bright_yellow: string;
  bright_green: string;
  bright_cyan: string;
  bright_blue: string;
  bright_magenta: string;
};

const HEX = /^#[0-9a-fA-F]{6}$/;

/**
 * The subset of TOML a `colors.toml` uses: `key = "value"` lines,
 * comments, blank lines. Omarchy parses these files with a shell loop,
 * so anything richer would not survive Omarchy either.
 */
export function parseColorsToml(text: string): RawPalette {
  const out: RawPalette = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim().replace(/["']/g, "");
    let value = line.slice(eq + 1).trim();
    const quote = value.match(/^["']/);
    if (quote) {
      const close = value.indexOf(quote[0], 1);
      value = close > 0 ? value.slice(1, close) : value.slice(1);
    } else {
      value = value.replace(/\s+#.*$/, "").trim();
    }
    if (!/^[A-Za-z0-9_-]+$/.test(key)) continue;
    out[key] = value;
  }
  return out;
}

/** Mix `start` toward `end` by `amount` in [0, 1], rounding like Omarchy's awk. */
export function mix(start: string, end: string, amount: number): string {
  const a = Math.max(0, Math.min(1, amount));
  const s = channels(start);
  const e = channels(end);
  const c = s.map((v, i) => Math.floor(v * (1 - a) + e[i]! * a + 0.5));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function channels(hex: string): [number, number, number] {
  const h = hex.replace(/^#/, "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

/** Omarchy's luminance rule: channel sum above 382 reads as light. */
export function isLightBackground(hex: string): boolean {
  if (!HEX.test(hex)) return false;
  const [r, g, b] = channels(hex);
  return r + g + b > 382;
}

/**
 * Resolve a raw palette. Mirrors `resolve_theme_colors` in
 * `omarchy-theme-color`, in the same order, so a theme that leaves
 * keys out gets the same colors here that its terminal shows.
 */
export function resolvePalette(raw: RawPalette): Palette {
  const c: RawPalette = { ...raw };
  const alias = (key: string, fallback: string) => {
    if (!c[key] && c[fallback]) c[key] = c[fallback]!;
  };

  // Legacy short names for the semantic families.
  const legacyPalette: Record<string, string> = {
    background: "bg",
    dark_background: "dark_bg",
    darker_background: "darker_bg",
    lighter_background: "lighter_bg",
    foreground: "fg",
    dark_foreground: "dark_fg",
    light_foreground: "light_fg",
    bright_foreground: "bright_fg",
  };
  for (const [key, legacy] of Object.entries(legacyPalette)) alias(key, legacy);

  // ANSI-only themes: color0/color7 stand in for the base pair.
  if (!c.background && c.color0) c.background = c.color0;
  if (!c.foreground && c.color7) c.foreground = c.color7;
  if (c.background) c.color0 = c.background;
  if (c.foreground) c.color7 = c.foreground;

  const legacyAnsi: Record<string, string> = {
    red: "color1",
    green: "color2",
    yellow: "color3",
    blue: "color4",
    magenta: "color5",
    cyan: "color6",
    bright_red: "color9",
    bright_green: "color10",
    bright_yellow: "color11",
    bright_blue: "color12",
    bright_magenta: "color13",
    bright_cyan: "color14",
  };
  for (const [key, legacy] of Object.entries(legacyAnsi)) alias(key, legacy);
  alias("magenta", "purple");
  alias("bright_magenta", "bright_purple");

  const bg = c.background ?? "#000000";
  const fg = c.foreground ?? "#ffffff";
  c.background = bg;
  c.foreground = fg;
  c.light_foreground ||= c.color7 || fg;
  c.bright_foreground ||= c.color15 || fg;
  c.cursor = c.bright_foreground;
  c.lighter_background ||= c.color0 || bg;
  c.dark_foreground ||= c.color8 || fg;
  c.muted ||= c.color8 || c.dark_foreground;
  c.selection ||= c.selection_background || c.color8 || c.color0 || bg;
  c.selection_background ||= c.selection;
  c.selection_foreground ||= c.bright_foreground;
  c.orange ||= c.yellow ?? fg;
  c.brown ||= mix(c.orange, "#000000", 0.5);

  c.dark_background ||= mix(bg, "#000000", 0.25);
  c.darker_background ||= mix(bg, "#000000", 0.5);
  for (const hue of ["red", "yellow", "green", "cyan", "blue", "magenta"]) {
    c[hue] ||= fg;
    c[`bright_${hue}`] ||= mix(c[hue]!, "#ffffff", 0.2);
  }
  c.accent ||= c.blue!;

  const mode = resolveMode(c);

  return {
    mode,
    accent: c.accent!,
    selection: c.selection!,
    selection_background: c.selection_background!,
    selection_foreground: c.selection_foreground!,
    muted: c.muted!,
    cursor: c.cursor!,
    background: c.background,
    dark_background: c.dark_background!,
    darker_background: c.darker_background!,
    lighter_background: c.lighter_background!,
    foreground: c.foreground,
    dark_foreground: c.dark_foreground!,
    light_foreground: c.light_foreground!,
    bright_foreground: c.bright_foreground!,
    red: c.red!,
    yellow: c.yellow!,
    orange: c.orange!,
    green: c.green!,
    cyan: c.cyan!,
    blue: c.blue!,
    magenta: c.magenta!,
    brown: c.brown!,
    bright_red: c.bright_red!,
    bright_yellow: c.bright_yellow!,
    bright_green: c.bright_green!,
    bright_cyan: c.bright_cyan!,
    bright_blue: c.bright_blue!,
    bright_magenta: c.bright_magenta!,
  };
}

/** `mode`, then legacy `theme_type`, then the background's luminance. */
function resolveMode(c: RawPalette): "dark" | "light" {
  const declared = (c.mode || c.theme_type || "").toLowerCase();
  if (declared === "light" || declared === "dark") return declared;
  return isLightBackground(c.background ?? "") ? "light" : "dark";
}
