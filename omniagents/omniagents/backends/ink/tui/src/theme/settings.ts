/**
 * The persisted theme choice: one JSON file the host names, holding
 * `{ "theme": "<id>" }`. Absent, or `"omarchy"`, means follow Omarchy
 * when it is there and fall back to the default otherwise.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type InkSettings = {
  theme?: string;
};

export function readSettings(file: string | undefined): InkSettings {
  if (!file || !existsSync(file)) return {};
  try {
    const parsed = JSON.parse(readFileSync(file, "utf8")) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as InkSettings) : {};
  } catch {
    return {};
  }
}

export function writeSettings(
  file: string | undefined,
  patch: InkSettings,
): void {
  if (!file) return;
  const merged = { ...readSettings(file), ...patch };
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify(merged, null, 2) + "\n");
}
