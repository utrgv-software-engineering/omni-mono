/**
 * The design system's single source of color truth.
 *
 * Components speak semantic ROLES, never colors, so "what does cyan
 * mean" has exactly one answer:
 *
 *   text*    — content tiers: primary payload, secondary values, chrome
 *   state*   — lifecycle: active (running NOW), done, failed, blocked
 *              (waiting on a human), idle (not started)
 *   accent*  — who is speaking: the user, the agent
 *   focus    — keyboard focus. Deliberately ≠ active.
 *   border*  — ambient chrome vs focused vs blocking surfaces
 *   bar*     — the filled top/bottom bars and their active tab block
 *
 * Diff additions are ALWAYS stateDone green and deletions stateFailed
 * red — no surface may invent its own diff colors.
 *
 * Which colors the roles resolve to is a THEME: an Omarchy `colors.toml`
 * palette mapped through `theme/roles.ts`. `Theme` reads the palette
 * that is current at render time, so `Theme.textPrimary` in a component
 * needs no change when the theme does; what the switch needs is a
 * re-render, which `themedMemo` and the store's theme version provide.
 * Nothing outside `theme/` may name a hex.
 */

import { resolvePalette } from "./theme/palette.js";
import { rolesFromPalette, type ThemeRoles } from "./theme/roles.js";
import { BUNDLED_THEMES } from "./theme/bundled.js";

export type { ThemeRole, ThemeRoles } from "./theme/roles.js";

/** The palette the TUI shipped with, and the fallback when nothing else resolves. */
export const DEFAULT_THEME_ID = "tokyo-night";

let current: ThemeRoles = rolesFromPalette(
  resolvePalette(BUNDLED_THEMES.find((t) => t.id === DEFAULT_THEME_ID)!.colors),
);

/** Replace the live roles. Callers bump the store's theme version after. */
export function setThemeRoles(roles: ThemeRoles): void {
  current = roles;
}

export function currentThemeRoles(): ThemeRoles {
  return current;
}

/**
 * Live role lookup. A plain object whose properties are getters over the
 * current roles: reads look like constants, resolve like calls.
 */
export const Theme: Readonly<ThemeRoles> = Object.freeze(
  Object.defineProperties(
    {},
    Object.fromEntries(
      (Object.keys(current) as Array<keyof ThemeRoles>).map((role) => [
        role,
        { get: () => current[role], enumerable: true },
      ]),
    ),
  ),
) as Readonly<ThemeRoles>;

/**
 * A map from your own keys to roles, resolved lazily. For module-level
 * tables like `STATUS_COLOR`: captured `Theme.x` values would freeze the
 * palette at import time, these read it per access.
 */
export function roleMap<K extends string>(
  spec: Record<K, keyof ThemeRoles>,
): Record<K, string> {
  return Object.defineProperties(
    {},
    Object.fromEntries(
      (Object.entries(spec) as Array<[K, keyof ThemeRoles]>).map(
        ([key, role]) => [key, { get: () => current[role], enumerable: true }],
      ),
    ),
  ) as Record<K, string>;
}
