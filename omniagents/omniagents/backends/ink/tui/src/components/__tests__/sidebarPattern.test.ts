/**
 * Guards the one-way-to-mark-a-selected-row rule.
 *
 * Selection had drifted to fourteen hand-rolled cursors across thirteen
 * files — half `"▶ "` (two columns), half `"▶"` (one), coloured three
 * different ways — because nothing stopped the next list from inventing
 * its own. `shared/SelectableRow` owns the treatment now, and this test
 * is what stops the fifteenth.
 *
 * A source scan rather than a lint rule: the repo already guards
 * contracts this way (`protocol/experimental.test.ts` diffs a manifest
 * against the protocol inventory), it costs one file, and it runs in the
 * suite everyone already runs.
 */

import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { describe, expect, it } from "vitest";

const COMPONENTS = fileURLToPath(new URL("..", import.meta.url));
const OWNER = path.join("shared", "SelectableRow.tsx");

/** Every component source file, relative to `components/`. */
function sourceFiles(dir = COMPONENTS, prefix = ""): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = path.join(prefix, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__") continue;
      out.push(...sourceFiles(path.join(dir, entry.name), rel));
    } else if (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) {
      out.push(rel);
    }
  }
  return out;
}

const files = sourceFiles().map((rel) => ({
  rel,
  source: readFileSync(path.join(COMPONENTS, rel), "utf-8"),
}));

describe("selected-row pattern", () => {
  it("scans a plausible number of component files", () => {
    // A traversal bug that found nothing would make every assertion
    // below pass by looking at an empty list.
    expect(files.length).toBeGreaterThan(20);
    expect(files.map((f) => f.rel)).toContain(OWNER);
  });

  it("leaves the cursor glyph to SelectableRow", () => {
    const offenders = files
      .filter((f) => f.rel !== OWNER && f.source.includes("▶"))
      .map((f) => f.rel);
    expect(
      offenders,
      "render rows through shared/SelectableRow instead of writing ▶ directly",
    ).toEqual([]);
  });

  it("leaves the selection band to SelectableRow", () => {
    const offenders = files
      .filter((f) => f.rel !== OWNER && f.source.includes("selectionBg"))
      .map((f) => f.rel);
    expect(
      offenders,
      "Theme.selectionBg is SelectableRow's; a list must not paint its own band",
    ).toEqual([]);
  });

  it("marks selection with the band and nothing else", () => {
    // A cursor glyph on top of a full-width highlight is belt-and-braces
    // that costs two columns on every row of every list. The band is the
    // marker, the way vim's cursorline is.
    const owner = files.find((f) => f.rel === OWNER)!.source;
    expect(owner).toContain("selectionBg");
    expect(owner).not.toContain("\u25b6");
  });
});
