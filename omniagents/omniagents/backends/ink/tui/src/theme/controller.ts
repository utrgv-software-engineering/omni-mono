/**
 * Chooses and applies the theme, and keeps following Omarchy when asked.
 *
 * Precedence for the initial theme: an explicit `--theme` flag, the
 * saved setting, `OMNI_THEME` in the environment, Omarchy's current
 * theme, the bundled default. Choosing "omarchy" (or nothing, on an
 * Omarchy machine) follows the desktop: the palette is re-read when
 * `omarchy-theme-set` runs.
 */

import { useAppStore } from "../store/appStore.js";
import { DEFAULT_THEME_ID, setThemeRoles } from "../theme.js";
import { rolesFromPalette } from "./roles.js";
import { writeSettings, readSettings } from "./settings.js";
import {
  FOLLOW_OMARCHY_ID,
  listThemes,
  omarchyCurrentTheme,
  watchOmarchyTheme,
  type ThemeSource,
} from "./sources.js";

export type ThemeHost = {
  themesDir?: string;
  settingsFile?: string;
  omarchyStateDir?: string;
  env?: NodeJS.ProcessEnv;
};

let host: ThemeHost = {};
let activeId: string = DEFAULT_THEME_ID;
let stopFollowing: (() => void) | null = null;

export function configureThemeHost(next: ThemeHost): void {
  host = next;
}

export function themeHost(): ThemeHost {
  return host;
}

export function activeThemeId(): string {
  return activeId;
}

export function availableThemes(): ThemeSource[] {
  return listThemes({
    themesDir: host.themesDir,
    omarchyStateDir: host.omarchyStateDir,
  });
}

/** Which theme id the precedence rules pick, before anything is applied. */
export function resolveInitialThemeId(
  flagTheme: string | undefined,
  h: ThemeHost = host,
): string {
  const env = h.env ?? process.env;
  const saved = readSettings(h.settingsFile).theme;
  const wanted = flagTheme || saved || env.OMNI_THEME;
  if (wanted) return wanted;
  return omarchyCurrentTheme(h.omarchyStateDir)
    ? FOLLOW_OMARCHY_ID
    : DEFAULT_THEME_ID;
}

/**
 * Apply a theme by id. Unknown ids fall back to the default so a stale
 * setting never leaves the TUI unpainted. Returns the source applied.
 */
export function applyTheme(id: string): ThemeSource {
  const themes = availableThemes();
  const source =
    themes.find((t) => t.id === id) ??
    themes.find((t) => t.id === DEFAULT_THEME_ID) ??
    themes[0]!;
  activeId = source.id;
  setThemeRoles(rolesFromPalette(source.palette));
  useAppStore.getState().actions.bumpThemeVersion();
  follow(source.id === FOLLOW_OMARCHY_ID);
  return source;
}

/** Apply and remember. */
export function selectTheme(id: string): ThemeSource {
  const source = applyTheme(id);
  writeSettings(host.settingsFile, { theme: source.id });
  return source;
}

function follow(enabled: boolean): void {
  if (!enabled) {
    stopFollowing?.();
    stopFollowing = null;
    return;
  }
  if (stopFollowing) return;
  stopFollowing = watchOmarchyTheme(() => {
    if (activeId !== FOLLOW_OMARCHY_ID) return;
    const source = omarchyCurrentTheme(host.omarchyStateDir);
    if (!source) return;
    setThemeRoles(rolesFromPalette(source.palette));
    useAppStore.getState().actions.bumpThemeVersion();
  }, host.omarchyStateDir);
}

/** Startup: configure, resolve, apply. */
export function initTheme(
  flagTheme: string | undefined,
  h: ThemeHost,
): ThemeSource {
  configureThemeHost(h);
  return applyTheme(resolveInitialThemeId(flagTheme, h));
}

/** Tests and shutdown. */
export function disposeTheme(): void {
  follow(false);
}
