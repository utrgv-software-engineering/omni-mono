/**
 * Theme system for lazyomni TUI.
 *
 * Defines a semantic color interface and theme presets ported from the
 * launcher's CSS themes + omniagents' ink TokyoNight theme.
 */

export interface Theme {
  // --- UI chrome ---
  accent: string;          // primary accent (focused items, highlights, brand)
  accentSecondary: string; // secondary accent (less prominent)
  border: string;          // panel/box borders (unfocused)
  borderFocused: string;   // focused border
  borderRaw: string;       // raw/insert-mode border

  // --- Text ---
  fg: string;              // default foreground
  fgMuted: string;         // secondary text (labels, metadata)
  fgSubtle: string;        // tertiary text (placeholders, help)

  // --- Semantic ---
  success: string;
  warning: string;
  error: string;
  info: string;

  // --- Mode indicator ---
  modeNormal: string;
  modeInsert: string;

  // --- Message roles ---
  roleUser: string;
  roleAssistant: string;
  roleTool: string;
  roleSystem: string;

  // --- Phases/status ---
  phaseRunning: string;
  phaseWaiting: string;
  phaseInput: string;
  phaseError: string;
  phaseIdle: string;
  phaseReady: string;

  // --- Priority ---
  priorityLow: string;
  priorityMedium: string;
  priorityHigh: string;
  priorityCritical: string;

  // --- Diff ---
  diffAdd: string;
  diffDelete: string;
  diffHunk: string;
}

// ─── Theme definitions ──────────────────────────────────────────────

export const tokyoNight: Theme = {
  accent: "#7aa2f7",
  accentSecondary: "#bb9af7",
  border: "#3b4261",
  borderFocused: "#7aa2f7",
  borderRaw: "#9ece6a",

  fg: "#c0caf5",
  fgMuted: "#a9b1d6",
  fgSubtle: "#545c7e",

  success: "#9ece6a",
  warning: "#e0af68",
  error: "#f7768e",
  info: "#7dcfff",

  modeNormal: "#7aa2f7",
  modeInsert: "#9ece6a",

  roleUser: "#7dcfff",
  roleAssistant: "#9ece6a",
  roleTool: "#e0af68",
  roleSystem: "#bb9af7",

  phaseRunning: "#9ece6a",
  phaseWaiting: "#e0af68",
  phaseInput: "#7aa2f7",
  phaseError: "#f7768e",
  phaseIdle: "#545c7e",
  phaseReady: "#7dcfff",

  priorityLow: "#545c7e",
  priorityMedium: "#7aa2f7",
  priorityHigh: "#e0af68",
  priorityCritical: "#f7768e",

  diffAdd: "#9ece6a",
  diffDelete: "#f7768e",
  diffHunk: "#7dcfff",
};

export const defaultDark: Theme = {
  accent: "#818cf8",
  accentSecondary: "#c084fc",
  border: "#3f3f46",
  borderFocused: "#818cf8",
  borderRaw: "#4ade80",

  fg: "#fafafa",
  fgMuted: "#a1a1aa",
  fgSubtle: "#71717a",

  success: "#4ade80",
  warning: "#facc15",
  error: "#f87171",
  info: "#2dd4bf",

  modeNormal: "#818cf8",
  modeInsert: "#4ade80",

  roleUser: "#2dd4bf",
  roleAssistant: "#4ade80",
  roleTool: "#facc15",
  roleSystem: "#c084fc",

  phaseRunning: "#4ade80",
  phaseWaiting: "#facc15",
  phaseInput: "#818cf8",
  phaseError: "#f87171",
  phaseIdle: "#71717a",
  phaseReady: "#2dd4bf",

  priorityLow: "#71717a",
  priorityMedium: "#818cf8",
  priorityHigh: "#facc15",
  priorityCritical: "#f87171",

  diffAdd: "#4ade80",
  diffDelete: "#f87171",
  diffHunk: "#2dd4bf",
};

export const vscodeDark: Theme = {
  accent: "#569cd6",
  accentSecondary: "#c586c0",
  border: "#3c3c3c",
  borderFocused: "#569cd6",
  borderRaw: "#0dbc79",

  fg: "#cccccc",
  fgMuted: "#bbbbbb",
  fgSubtle: "#9d9d9d",

  success: "#0dbc79",
  warning: "#cca700",
  error: "#f85149",
  info: "#4ec9b0",

  modeNormal: "#569cd6",
  modeInsert: "#0dbc79",

  roleUser: "#4ec9b0",
  roleAssistant: "#0dbc79",
  roleTool: "#cca700",
  roleSystem: "#c586c0",

  phaseRunning: "#0dbc79",
  phaseWaiting: "#cca700",
  phaseInput: "#569cd6",
  phaseError: "#f85149",
  phaseIdle: "#9d9d9d",
  phaseReady: "#4ec9b0",

  priorityLow: "#9d9d9d",
  priorityMedium: "#569cd6",
  priorityHigh: "#cca700",
  priorityCritical: "#f85149",

  diffAdd: "#0dbc79",
  diffDelete: "#f85149",
  diffHunk: "#4ec9b0",
};

export const vscodeLight: Theme = {
  accent: "#0451a5",
  accentSecondary: "#af00db",
  border: "#d4d4d4",
  borderFocused: "#0451a5",
  borderRaw: "#2ea043",

  fg: "#1f1f1f",
  fgMuted: "#3b3b3b",
  fgSubtle: "#767676",

  success: "#2ea043",
  warning: "#cca700",
  error: "#f85149",
  info: "#267f99",

  modeNormal: "#0451a5",
  modeInsert: "#2ea043",

  roleUser: "#267f99",
  roleAssistant: "#2ea043",
  roleTool: "#cca700",
  roleSystem: "#af00db",

  phaseRunning: "#2ea043",
  phaseWaiting: "#cca700",
  phaseInput: "#0451a5",
  phaseError: "#f85149",
  phaseIdle: "#767676",
  phaseReady: "#267f99",

  priorityLow: "#767676",
  priorityMedium: "#0451a5",
  priorityHigh: "#cca700",
  priorityCritical: "#f85149",

  diffAdd: "#2ea043",
  diffDelete: "#f85149",
  diffHunk: "#267f99",
};

export const utrgv: Theme = {
  accent: "#f05023",
  accentSecondary: "#8b3fc7",
  border: "#d9d9dc",
  borderFocused: "#f05023",
  borderRaw: "#1a7f37",

  fg: "#1a1a1e",
  fgMuted: "#646469",
  fgSubtle: "#8e8e93",

  success: "#1a7f37",
  warning: "#9a6700",
  error: "#dc2626",
  info: "#0e7c86",

  modeNormal: "#0451a5",
  modeInsert: "#1a7f37",

  roleUser: "#0e7c86",
  roleAssistant: "#1a7f37",
  roleTool: "#9a6700",
  roleSystem: "#8b3fc7",

  phaseRunning: "#1a7f37",
  phaseWaiting: "#9a6700",
  phaseInput: "#0451a5",
  phaseError: "#dc2626",
  phaseIdle: "#8e8e93",
  phaseReady: "#0e7c86",

  priorityLow: "#8e8e93",
  priorityMedium: "#0451a5",
  priorityHigh: "#9a6700",
  priorityCritical: "#dc2626",

  diffAdd: "#1a7f37",
  diffDelete: "#dc2626",
  diffHunk: "#0e7c86",
};

// ─── Registry ───────────────────────────────────────────────────────

export type ThemeName = "tokyo-night" | "default" | "vscode-dark" | "vscode-light" | "utrgv";

export const themes: Record<ThemeName, Theme> = {
  "tokyo-night": tokyoNight,
  "default": defaultDark,
  "vscode-dark": vscodeDark,
  "vscode-light": vscodeLight,
  "utrgv": utrgv,
};

export const themeNames = Object.keys(themes) as ThemeName[];
