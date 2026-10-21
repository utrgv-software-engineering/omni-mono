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

  it("marks selection with the band and bold, and no glyph", () => {
    // neo-tree's NeoTreeCursorLine is CursorLine + bold; matching it
    // means the band and the weight, not a caret in the gutter.
    const owner = files.find((f) => f.rel === OWNER)!.source;
    expect(owner).toContain("selectionBg");
    expect(owner).toContain("bold");
    expect(owner).not.toContain("\u25b6");
  });

  it("renders row text through RowText, so bold reaches the whole row", () => {
    // Ink cannot cascade bold from a Box, and a row cannot be wrapped in
    // a Text (it throws on the Box inside). RowText reads the row's
    // selected state from context instead — a bare Text inside a row
    // would silently stay unbolded while everything beside it bolds.
    const offenders: string[] = [];
    for (const { rel, source } of files) {
      if (rel === OWNER) continue;
      const lines = source.split("\n");
      for (let i = 0; i < lines.length; i += 1) {
        if (!lines[i]!.includes("<SelectableRow")) continue;
        for (let j = i + 1; j < lines.length; j += 1) {
          if (lines[j]!.includes("</SelectableRow>")) break;
          if (/<Text\b/.test(lines[j]!)) {
            offenders.push(`${rel}:${j + 1}`);
          }
        }
      }
    }
    expect(offenders, "use RowText inside a SelectableRow").toEqual([]);
  });

  it("gives every truncating column an explicit flexBasis", () => {
    // A `flexGrow` column with the default `auto` basis starts from its
    // text's own width. A name longer than the row never shrinks far
    // enough before Ink measures it, so `wrap` never gets to truncate and
    // the row silently becomes two lines — a blank gap in the sidebar.
    const offenders: string[] = [];
    for (const { rel, source } of files) {
      const lines = source.split("\n");
      lines.forEach((line, i) => {
        if (!line.includes("flexGrow")) return;
        if (!line.includes("minWidth={0}")) return;
        if (line.includes("flexBasis")) return;
        offenders.push(`${rel}:${i + 1}`);
      });
    }
    expect(
      offenders,
      "a truncating column needs flexBasis={0} or it wraps instead",
    ).toEqual([]);
  });
});
