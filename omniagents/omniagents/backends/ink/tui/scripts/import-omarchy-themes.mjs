#!/usr/bin/env node
/**
 * Vendor Omarchy's theme palettes into `src/theme/bundled.ts`.
 *
 *   node scripts/import-omarchy-themes.mjs /path/to/omarchy
 *
 * Reads every `themes/<name>/colors.toml` and writes them as data, raw
 * and unresolved: the TUI resolves them at runtime with the same cascade
 * Omarchy uses, so a re-import never needs to know what changed in the
 * resolver. Omarchy is MIT-licensed (Copyright David Heinemeier Hansson);
 * the notice travels with the data.
 */
import { readdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = process.argv[2];
if (!root || !existsSync(join(root, "themes"))) {
  console.error("usage: import-omarchy-themes.mjs <omarchy checkout>");
  process.exit(2);
}

const parse = (text) => {
  const out = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim().replace(/["']/g, "");
    let value = line.slice(eq + 1).trim();
    const q = value.match(/^["']/);
    if (q) {
      const close = value.indexOf(q[0], 1);
      value = close > 0 ? value.slice(1, close) : value.slice(1);
    } else value = value.replace(/\s+#.*$/, "").trim();
    if (/^[A-Za-z0-9_-]+$/.test(key)) out[key] = value;
  }
  return out;
};

const title = (id) =>
  id
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

const themes = [];
for (const id of readdirSync(join(root, "themes")).sort()) {
  const file = join(root, "themes", id, "colors.toml");
  if (!existsSync(file)) continue;
  themes.push({
    id,
    name: title(id),
    colors: parse(readFileSync(file, "utf8")),
  });
}

const license = readFileSync(join(root, "LICENSE"), "utf8")
  .split("\n")
  .slice(0, 1)
  .join("\n")
  .trim();

const out = `/**
 * Omarchy's theme palettes, vendored by scripts/import-omarchy-themes.mjs.
 * Do not edit: re-run the script against an Omarchy checkout.
 *
 * Source: https://github.com/basecamp/omarchy (themes/<name>/colors.toml)
 * ${license.replace(/\n/g, "\n * ")}
 * Licensed under the MIT License; see the Omarchy repository for the full text.
 */

import type { RawPalette } from "./palette.js";

export type BundledTheme = {
  id: string;
  name: string;
  colors: RawPalette;
};

export const BUNDLED_THEMES: readonly BundledTheme[] = ${JSON.stringify(themes, null, 2)};
`;

const here = dirname(fileURLToPath(import.meta.url));
const dest = resolve(here, "../src/theme/bundled.ts");
writeFileSync(dest, out);
console.log(`wrote ${themes.length} themes to ${dest}`);
