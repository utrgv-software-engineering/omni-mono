/**
 * From a resolved Omarchy palette to the TUI's semantic roles.
 *
 * The design system speaks roles, never colors (see `theme.ts`). This is
 * the one place that decides what "the active color" or "the ambient
 * border" is in a given palette, so a new theme needs no per-component
 * work. The mapping is chosen so Omarchy's Tokyo Night lands on the
 * hand-picked palette the TUI shipped with, and so light themes read
 * without special cases: every choice is relative to the theme's own
 * foreground and background.
 */

import { mix, type Palette } from "./palette.js";

export type ThemeRoles = {
  textPrimary: string;
  textSecondary: string;
  textChrome: string;
  stateActive: string;
  stateDone: string;
  stateFailed: string;
  stateBlocked: string;
  stateIdle: string;
  accentUser: string;
  accentAgent: string;
  focus: string;
  borderAmbient: string;
  borderFocus: string;
  borderBlocking: string;
  selectionBg: string;
  barBg: string;
  barActiveBg: string;
  barActiveFg: string;
};

export type ThemeRole = keyof ThemeRoles;

export function rolesFromPalette(p: Palette): ThemeRoles {
  return {
    textPrimary: p.bright_foreground,
    textSecondary: mix(p.foreground, p.background, 0.25),
    textChrome: p.dark_foreground,
    stateActive: p.bright_cyan,
    stateDone: p.green,
    stateFailed: p.red,
    stateBlocked: p.yellow,
    stateIdle: p.dark_foreground,
    accentUser: p.bright_magenta,
    accentAgent: p.accent,
    focus: p.bright_magenta,
    borderAmbient: p.muted,
    borderFocus: p.bright_magenta,
    borderBlocking: p.yellow,
    selectionBg: p.selection,
    barBg: p.lighter_background,
    barActiveBg: p.accent,
    barActiveFg: p.background,
  };
}
