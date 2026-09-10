// Pure helpers behind the theme drawer.

import type { ThemeSource } from "../theme/sources.js";

/** Name-prefix first, then substring, like the other pickers. */
export function filterThemes(
  rows: readonly ThemeSource[],
  query: string,
): ThemeSource[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...rows];
  const prefix = rows.filter((r) => r.name.toLowerCase().startsWith(needle));
  const rest = rows.filter(
    (r) =>
      !prefix.includes(r) &&
      (r.name.toLowerCase().includes(needle) || r.id.includes(needle)),
  );
  return [...prefix, ...rest];
}

/** Where the cursor starts: on the active theme. */
export function initialThemeIndex(
  rows: readonly ThemeSource[],
  activeId: string,
): number {
  const i = rows.findIndex((r) => r.id === activeId);
  return i < 0 ? 0 : i;
}

export function originLabel(source: ThemeSource): string {
  switch (source.origin) {
    case "omarchy":
      return source.detail
        ? `follows the desktop · ${source.detail}`
        : "follows the desktop";
    case "user":
      return "user";
    default:
      return source.palette.mode;
  }
}
