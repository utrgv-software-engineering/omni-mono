/**
 * Single source of truth for which sandbox profiles the launcher offers
 * the user. Settings UI (default-profile picker) and the per-launch
 * SandboxPicker both consume this so they never drift.
 *
 * Options come from backend discovery (`$sandboxProfiles`, filled by
 * `sandbox:list-profiles`) so user-created YAMLs under ``<config>/sandbox/``
 * appear everywhere. The hard-coded list survives only as the fallback
 * while the atom is empty (pre-first-fetch / fetch error), so no picker
 * regresses to an empty menu.
 */

import { $sandboxProfiles, $substrateStatus } from '@/renderer/features/Sandboxes/state';
import { HIDDEN_SANDBOX_PROFILES } from '@/shared/sandbox-profiles';
import type { ProfileSummary, SandboxSubstrateStatus } from '@/shared/types';

const FALLBACK_PROFILES = ['host', 'devbox'] as const;

const PROFILE_LABELS: Record<string, string> = {
  host: 'My computer (no sandbox)',
  devbox: 'Workstation (desktop + tools)',
  wasmbox: 'Mini computer (instant, sealed)',
};

/** First line in two-line pickers and the compact pill: just the name. */
const PROFILE_TITLES: Record<string, string> = {
  host: 'My computer',
  devbox: 'Workstation',
  wasmbox: 'Mini computer',
};

/**
 * Second line in two-line pickers (SandboxPicker menu). Deliberately lay
 * language — this line carries the safety tradeoff, so no Docker/wasm/
 * container vocabulary.
 */
const PROFILE_DESCRIPTIONS: Record<string, string> = {
  host: 'Full access to your files and programs. No sandbox.',
  devbox: 'A full separate computer with a desktop and tools installed.',
  wasmbox: 'Starts instantly, safely sealed off. Limited features.',
};

export type ProfileListContext = {
  /**
   * Backend-provided profile list (``StoreData.availableSandboxProfiles``).
   * When set, it's authoritative — a deployment can restrict the picker to
   * a subset and hide host/devbox.
   */
  available?: string[];
  /**
   * Discovered profile catalog. Defaults to a non-reactive `$sandboxProfiles`
   * read; pass a
   * `useStore($sandboxProfiles)` value where the options must re-render as
   * discovery lands.
   */
  discovered?: ProfileSummary[];
  /**
   * Backend substrate probe (`sandbox:substrate-status`). Docker-backed
   * profiles are hidden when Docker is missing. Defaults to a non-reactive
   * `$substrateStatus` read; pass `useStore($substrateStatus)` to re-render
   * when the probe lands. Null (not probed yet) hides nothing.
   */
  substrate?: SandboxSubstrateStatus | null;
};

/**
 * Whether *name*'s runtime requirements are met on the backend. Docker
 * profiles need Docker installed ("daemon-down" stays offered: starting
 * Docker fixes it, and the launch error says so). Unknown probe results and
 * client types never hide a profile.
 */
const meetsRequirements = (
  name: string,
  discovered: ProfileSummary[],
  substrate: SandboxSubstrateStatus | null | undefined
): boolean => {
  const clientType = discovered.find((p) => p.name === name)?.clientType ?? (name === 'devbox' ? 'docker' : undefined);
  if (clientType === 'docker') {
    return substrate?.docker !== 'missing';
  }
  return true;
};

/**
 * Safety-first display rank: sealed → contained → user-created (unknown
 * containment) → unsandboxed. Every picker sorts by this so the safe choices lead
 * and "no sandbox" always sits at the bottom — order is part of the risk
 * signaling, alongside the warning tint and the description line.
 */
const profileSafetyRank = (name: string): number => {
  if (name === 'wasmbox') {
    return 0;
  }
  if (name === 'devbox') {
    return 1;
  }
  if (name === 'host') {
    return 3;
  }
  return 2;
};

export const getAvailableProfileNames = (ctx: ProfileListContext): string[] => {
  const discovered = ctx.discovered ?? $sandboxProfiles.get();
  const substrate = ctx.substrate === undefined ? $substrateStatus.get() : ctx.substrate;
  const offered = (names: string[]): string[] =>
    names
      .filter((name) => !HIDDEN_SANDBOX_PROFILES.has(name) && meetsRequirements(name, discovered, substrate))
      .sort((a, b) => profileSafetyRank(a) - profileSafetyRank(b));
  // Deployment restriction stays authoritative for MEMBERSHIP. Presentation
  // order is ours: safest first.
  // Array.prototype.sort is stable, so same-rank entries keep their
  // discovered/declared relative order.
  if (ctx.available && ctx.available.length > 0) {
    return offered([...ctx.available]);
  }
  if (discovered.length > 0) {
    return offered(discovered.map((p) => p.name));
  }
  return offered([...FALLBACK_PROFILES]);
};

const titleCase = (s: string): string => (s.length === 0 ? s : s[0]!.toUpperCase() + s.slice(1));

/**
 * Long-form label used in pickers and settings ("My computer (no sandbox)"). For
 * compact status chips (e.g. "Workstation") use ``buildProfileLabel`` from
 * ``@/renderer/omniagents-ui/sandbox-label``.
 */
export const getProfileMenuLabel = (name: string): string => PROFILE_LABELS[name] ?? resolveDynamicLabel(name);

/**
 * Title-only label ("Workstation") for two-line menu items and compact
 * pills; pair with ``getProfileDescription`` for the explanatory line.
 * Unknown names resolve exactly like ``getProfileMenuLabel``.
 */
export const getProfileTitle = (name: string): string => PROFILE_TITLES[name] ?? resolveDynamicLabel(name);

/** Explanatory second line for known profiles; null for user-created names. */
export const getProfileDescription = (name: string): string | null => PROFILE_DESCRIPTIONS[name] ?? null;

const resolveDynamicLabel = (name: string): string => {
  // Names outside the hard-coded map (user-created profiles) get their
  // catalog label from discovery before the title-case fallback.
  const discovered = $sandboxProfiles.get().find((p) => p.name === name);
  return discovered?.label ?? titleCase(name);
};
