/**
 * The design system's single source of color truth.
 *
 * Two layers, LazyVim-style: a private PALETTE (raw Tokyo Night hexes —
 * nothing outside this file may name a hex) and public SEMANTIC ROLES.
 * Components speak roles, never colors, so "what does cyan mean" has
 * exactly one answer:
 *
 *   text*    — content tiers: primary payload, secondary values, chrome
 *   state*   — lifecycle: active (running NOW), done, failed, blocked
 *              (waiting on a human), idle (not started)
 *   accent*  — who is speaking: the user, the agent
 *   focus    — keyboard focus. Purple, deliberately ≠ active cyan.
 *   border*  — ambient chrome vs focused vs blocking surfaces
 *   bar*     — the filled top/bottom bars and their active tab block
 *
 * Diff additions are ALWAYS stateDone green and deletions stateFailed
 * red — no surface may invent its own diff colors.
 */

const palette = {
  fg: "#c0caf5",
  fgDim: "#7f8ab0",
  fgMuted: "#565f89",
  bgHighlight: "#24283b",
  bgDark: "#1a1b26",
  borderLine: "#3b4261",
  blue: "#7aa2f7",
  cyan: "#7dcfff",
  purple: "#bb9af7",
  green: "#9ece6a",
  red: "#f7768e",
  yellow: "#e0af68",
} as const;

export const Theme = {
  // Content tiers
  textPrimary: palette.fg,
  textSecondary: palette.fgDim,
  textChrome: palette.fgMuted,

  // Lifecycle states
  stateActive: palette.cyan,
  stateDone: palette.green,
  stateFailed: palette.red,
  stateBlocked: palette.yellow,
  stateIdle: palette.fgMuted,

  // Voices
  accentUser: palette.purple,
  accentAgent: palette.blue,

  // Keyboard focus — never cyan, so focus ≠ activity
  focus: palette.purple,

  // Chrome
  borderAmbient: palette.borderLine,
  borderFocus: palette.purple,
  borderBlocking: palette.yellow,

  // Selected row in any list or sidebar. Deliberately a MUTED band
  // rather than a saturated fill: every list already picks its own
  // foreground per row (state colors, dimmed paths, diff green/red), and
  // a bright background would have required re-checking contrast at
  // fourteen call sites. bgHighlight sits one step off the terminal
  // ground, so every existing foreground still reads on it.
  //
  // The band is never the ONLY signal — see shared/SelectableRow: the
  // glyph and bold ride along, because chalk drops backgrounds entirely
  // under NO_COLOR and a colour-only selection would vanish there.
  selectionBg: palette.bgHighlight,

  // Filled bars (top tabs / bottom status)
  barBg: palette.bgHighlight,
  barActiveBg: palette.blue,
  barActiveFg: palette.bgDark,
} as const;
