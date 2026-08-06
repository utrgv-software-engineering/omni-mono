import { describe, expect, it } from "vitest";
import {
  buildReviewView,
  changeGlyph,
  fileBadge,
  numberedFromHunks,
  splitUnifiedDiff,
} from "../review-model.js";
import type { GitDiffHunk } from "../../rpc/client.js";

describe("buildReviewView", () => {
  const files = [
    { path: "a.ts", change: "modified", additions: 2, deletions: 1 },
    { path: "b.ts", change: "added", additions: 5, deletions: null },
  ];

  it("concatenates styled file headers with hunks, dropping raw noise", () => {
    const { lines, sections } = buildReviewView(files, (index) =>
      index === 0
        ? [
            { text: "diff --git a/a.ts b/a.ts", color: "x" },
            { text: "+++ b/a.ts", color: "x" },
            { text: "@@ -1 +1 @@", color: "x", bold: true },
            { text: "+new", color: "x" },
          ]
        : [{ text: "+all new", color: "x" }],
    );
    expect(lines.map((l) => l.text)).toEqual([
      "▌ M a.ts  +2 −1",
      "@@ -1 +1 @@",
      "+new",
      "",
      "▌ A b.ts  +5",
      "+all new",
    ]);
    expect(sections).toEqual([
      { path: "a.ts", offset: 0 },
      { path: "b.ts", offset: 4 },
    ]);
  });
});

describe("numberedFromHunks", () => {
  it("uses the server's per-line numbers", () => {
    const hunk: GitDiffHunk = {
      hunk_id: "h",
      index: 0,
      header: "@@ -5,2 +5,2 @@",
      section_heading: "",
      old_start: 5,
      old_lines: 2,
      new_start: 5,
      new_lines: 2,
      lines: [
        { origin: "context", content: "a", old_lineno: 5, new_lineno: 5 },
        { origin: "delete", content: "gone", old_lineno: 6, new_lineno: null },
        { origin: "add", content: "here", old_lineno: null, new_lineno: 6 },
      ],
    };
    expect(numberedFromHunks([hunk]).map((l) => l.text)).toEqual([
      "   ⋯",
      "   5   a",
      "     - gone",
      "   6 + here",
    ]);
  });
});

describe("fileBadge", () => {
  it("renders XY staging state with dots for spaces", () => {
    expect(
      fileBadge({
        path: "a",
        change: "modified",
        additions: 1,
        deletions: 0,
        xy: "M ",
        staged: true,
        unstaged: false,
      }).text,
    ).toBe("M·");
    expect(
      fileBadge({
        path: "b",
        change: "modified",
        additions: 1,
        deletions: 0,
        xy: "??",
        untracked: true,
      }).text,
    ).toBe("??");
  });

  it("falls back to the change glyph without XY state", () => {
    expect(
      fileBadge({ path: "c", change: "added", additions: 1, deletions: null })
        .text,
    ).toBe("A ");
  });
});

describe("splitUnifiedDiff", () => {
  it("splits a multi-file unified diff by post-image path", () => {
    const diff = [
      "diff --git a/src/a.ts b/src/a.ts",
      "index 111..222 100644",
      "--- a/src/a.ts",
      "+++ b/src/a.ts",
      "@@ -1 +1 @@",
      "+new",
      "diff --git a/old.ts b/renamed.ts",
      "@@ -2 +2 @@",
      "-gone",
    ].join("\n");
    const byPath = splitUnifiedDiff(diff);
    expect([...byPath.keys()]).toEqual(["src/a.ts", "renamed.ts"]);
    expect(byPath.get("src/a.ts")).toContain("+new");
    expect(byPath.get("renamed.ts")).toEqual(["@@ -2 +2 @@", "-gone"]);
  });

  it("returns an empty map for an empty diff", () => {
    expect(splitUnifiedDiff("").size).toBe(0);
  });
});

describe("changeGlyph", () => {
  it("covers the git change vocabulary with a fallback", () => {
    expect(changeGlyph("added")).toBe("A");
    expect(changeGlyph("deleted")).toBe("D");
    expect(changeGlyph("renamed")).toBe("R");
    expect(changeGlyph("mystery")).toBe("M");
  });
});
