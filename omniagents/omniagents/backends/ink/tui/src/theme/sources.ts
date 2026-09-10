/**
 * Where themes come from, in the order the picker lists them.
 *
 * - Omarchy's current theme, when this machine runs Omarchy: the palette
 *   under `~/.local/state/omarchy/current/theme/colors.toml`, replaced
 *   wholesale by `omarchy-theme-set`. Following it is what makes the TUI
 *   change with the desktop, the way Omarchy retints Claude Code and Pi
 *   by dropping a generated theme into their config directories.
 * - User themes: any `<themes dir>/<name>.toml` or `<name>/colors.toml`
 *   in Omarchy's format. Where the themes dir is comes from the host.
 * - Bundled: Omarchy's own themes, vendored in `bundled.ts`.
 */

import {
  existsSync,
  readdirSync,
  readFileSync,
  statSync,
  watch,
} from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";
import { BUNDLED_THEMES } from "./bundled.js";
import { parseColorsToml, resolvePalette, type Palette } from "./palette.js";

export type ThemeOrigin = "omarchy" | "user" | "bundled";

export type ThemeSource = {
  id: string;
  name: string;
  origin: ThemeOrigin;
  palette: Palette;
  /** Omarchy's own name for it, when following the desktop. */
  detail?: string;
};

/** The id of the pseudo-theme that follows Omarchy's current theme. */
export const FOLLOW_OMARCHY_ID = "omarchy";

export function omarchyStateDir(home = homedir()): string {
  return join(home, ".local", "state", "omarchy", "current");
}

/** Omarchy's current palette, or null when this machine is not running Omarchy. */
export function omarchyCurrentTheme(
  stateDir = omarchyStateDir(),
): ThemeSource | null {
  const file = join(stateDir, "theme", "colors.toml");
  if (!existsSync(file)) return null;
  let name = "";
  try {
    name = readFileSync(join(stateDir, "theme.name"), "utf8").trim();
  } catch {
    name = "";
  }
  try {
    return {
      id: FOLLOW_OMARCHY_ID,
      name: "Omarchy",
      origin: "omarchy",
      palette: resolvePalette(parseColorsToml(readFileSync(file, "utf8"))),
      detail: name ? titleCase(name) : undefined,
    };
  } catch {
    return null;
  }
}

/**
 * Watch Omarchy's state dir for a theme switch. `omarchy-theme-set`
 * replaces the `theme` directory and rewrites `theme.name`, so a change
 * to either is the signal. Debounced, because the switch is several
 * filesystem operations. Returns a disposer.
 */
export function watchOmarchyTheme(
  onChange: () => void,
  stateDir = omarchyStateDir(),
): () => void {
  if (!existsSync(stateDir)) return () => {};
  let timer: ReturnType<typeof setTimeout> | null = null;
  let watcher: ReturnType<typeof watch> | null = null;
  try {
    watcher = watch(stateDir, { persistent: false }, () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        onChange();
      }, 250);
    });
    watcher.on("error", () => {});
  } catch {
    return () => {};
  }
  return () => {
    if (timer) clearTimeout(timer);
    watcher?.close();
  };
}

/** User themes: `<dir>/<name>.toml` or `<dir>/<name>/colors.toml`. */
export function userThemes(themesDir: string | undefined): ThemeSource[] {
  if (!themesDir || !existsSync(themesDir)) return [];
  const out: ThemeSource[] = [];
  let entries: string[];
  try {
    entries = readdirSync(themesDir).sort();
  } catch {
    return [];
  }
  for (const entry of entries) {
    const full = join(themesDir, entry);
    let file: string | null = null;
    let id = entry;
    try {
      if (statSync(full).isDirectory()) {
        const candidate = join(full, "colors.toml");
        if (existsSync(candidate)) file = candidate;
      } else if (entry.endsWith(".toml")) {
        file = full;
        id = basename(entry, ".toml");
      }
    } catch {
      continue;
    }
    if (!file) continue;
    try {
      out.push({
        id,
        name: titleCase(id),
        origin: "user",
        palette: resolvePalette(parseColorsToml(readFileSync(file, "utf8"))),
      });
    } catch {
      // A broken theme file is skipped, not fatal.
    }
  }
  return out;
}

export function bundledThemes(): ThemeSource[] {
  return BUNDLED_THEMES.map((t) => ({
    id: t.id,
    name: t.name,
    origin: "bundled",
    palette: resolvePalette(t.colors),
  }));
}

/** Every selectable theme: Omarchy first when present, then user, then bundled. A user theme shadows a bundled one of the same id. */
export function listThemes(options: {
  themesDir?: string;
  omarchyStateDir?: string;
}): ThemeSource[] {
  const out: ThemeSource[] = [];
  const omarchy = omarchyCurrentTheme(options.omarchyStateDir);
  if (omarchy) out.push(omarchy);
  const user = userThemes(options.themesDir);
  out.push(...user);
  const shadowed = new Set(user.map((t) => t.id));
  out.push(...bundledThemes().filter((t) => !shadowed.has(t.id)));
  return out;
}

export function titleCase(id: string): string {
  return id
    .split("-")
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}
