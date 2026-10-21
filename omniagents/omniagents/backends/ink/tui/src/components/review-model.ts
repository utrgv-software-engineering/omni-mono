// Presentation logic for the Review screen. Pure and unit-testable
// (same split as ./git-model / ./strip-model).

import type { GitDiffHunk } from "../rpc/client.js";
import { Theme } from "../theme.js";
import type { DiffViewLine } from "./git-model.js";

/** One entry in the review file list, filter-agnostic. */
export type ReviewFile = {
  path: string;
  change: string;
  additions: number | null;
  deletions: number | null;
  /** Working-filter staging state (git porcelain XY + flags). */
  xy?: string;
  staged?: boolean;
  unstaged?: boolean;
  untracked?: boolean;
  conflicted?: boolean;
};

/**
 * The two-character badge for a file row: the porcelain XY code on the
 * working filter (colored by staging state), the change glyph elsewhere.
 */
export function fileBadge(file: ReviewFile): { text: string; color: string } {
  if (file.xy !== undefined) {
    const color = file.conflicted
      ? Theme.stateFailed
      : file.untracked
        ? Theme.textChrome
        : file.staged && !file.unstaged
          ? Theme.stateDone
          : file.staged && file.unstaged
            ? Theme.stateBlocked
            : Theme.stateActive;
    return { text: file.xy.replace(/ /g, "·"), color };
  }
  return {
    text: changeGlyph(file.change).padEnd(2),
    color: changeColor(file.change),
  };
}

const CHANGE_GLYPH: Record<string, string> = {
  added: "A",
  modified: "M",
  deleted: "D",
  renamed: "R",
  copied: "C",
  type_changed: "T",
  unmerged: "U",
};

export function changeGlyph(change: string): string {
  return CHANGE_GLYPH[change] ?? "M";
}

export function changeColor(change: string): string {
  if (change === "added") return Theme.stateDone;
  if (change === "deleted") return Theme.stateFailed;
  if (change === "unmerged") return Theme.stateFailed;
  return Theme.stateActive;
}

export type UnifiedSection = {
  path: string;
  /** Index of the section's first line within the full line array. */
  offset: number;
};

const NOISE_PREFIXES = [
  "diff --git ",
  "index ",
  "--- ",
  "+++ ",
  "new file mode",
  "deleted file mode",
  "old mode",
  "new mode",
];

// ----- line-number gutter -----
//
// ONE column: the line's number in the file AS IT EXISTS NOW. Deleted
// lines carry no number — they don't exist in the current file; they
// are events between lines, marked only by `-`.

const NUM_WIDTH = 4;

function gutter(newNo: number | null, marker: string): string {
  const num =
    newNo === null ? " ".repeat(NUM_WIDTH) : String(newNo).padStart(NUM_WIDTH);
  return `${num} ${marker} `;
}

function hunkSeparator(heading: string | null): DiffViewLine {
  return {
    text: `${" ".repeat(NUM_WIDTH - 1)}⋯${heading ? `  ${heading}` : ""}`,
    color: Theme.textChrome,
  };
}

/**
 * Structured hunks → numbered display lines. Hunk boundaries render as
 * a quiet `⋯` (with git's section heading when present) instead of
 * `@@` math.
 */
export function numberedFromHunks(hunks: GitDiffHunk[]): DiffViewLine[] {
  const out: DiffViewLine[] = [];
  for (const hunk of hunks) {
    out.push(hunkSeparator(hunk.section_heading || null));
    for (const line of hunk.lines) {
      switch (line.origin) {
        case "add":
          out.push({
            text: gutter(line.new_lineno, "+") + line.content,
            color: Theme.stateDone,
          });
          break;
        case "delete":
          out.push({
            text: gutter(null, "-") + line.content,
            color: Theme.stateFailed,
          });
          break;
        case "no_newline":
          out.push({
            text: gutter(null, " ") + "\\ no newline at end of file",
            color: Theme.textChrome,
          });
          break;
        default:
          out.push({
            text: gutter(line.new_lineno, " ") + line.content,
            color: Theme.textSecondary,
          });
      }
    }
  }
  if (out.length === 0) {
    out.push({ text: "(no textual changes)", color: Theme.textChrome });
  }
  return out;
}

/**
 * One unified diff covering many files (the run diff's `diff` field) →
 * per-file raw line arrays, keyed by the post-image path. Files listed
 * in `files` but absent here (truncation) simply have no entry.
 */
export function splitUnifiedDiff(diff: string): Map<string, string[]> {
  const byPath = new Map<string, string[]>();
  let current: string[] | null = null;
  for (const line of diff.split("\n")) {
    const header = line.match(/^diff --git a\/(.*) b\/(.*)$/);
    if (header) {
      current = [];
      byPath.set(header[2]!, current);
      continue;
    }
    current?.push(line);
  }
  return byPath;
}

const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/;

/**
 * Raw unified-diff text (one file's section) → numbered display lines.
 * The `@@` headers seed the counter and render as `⋯` separators.
 *
 * Noise is dropped by grammar, not by prefix list: everything before the
 * section's first `@@` is file-header material (`index …`, `--- `/`+++ `,
 * git's `new file mode 100644`, the run-diff builder's bare `new file`,
 * `Binary files … differ`, rename/copy markers), and inside a hunk only
 * `+` / `-` / `\\` / space lines are content. A prefix list can't keep up
 * with header variants — treating "n" as context is how a bare `new file`
 * once rendered as the mystery line `0 ew file`.
 */
export function numberUnified(rawLines: string[]): DiffViewLine[] {
  const out: DiffViewLine[] = [];
  let newNo = 0;
  let inHunk = false;
  for (const line of rawLines) {
    const header = line.match(HUNK_HEADER);
    if (header) {
      inHunk = true;
      newNo = parseInt(header[2]!, 10);
      out.push(hunkSeparator(header[3]?.trim() || null));
      continue;
    }
    if (!inHunk) continue;
    if (line.startsWith("+")) {
      out.push({
        text: gutter(newNo, "+") + line.slice(1),
        color: Theme.stateDone,
      });
      newNo += 1;
    } else if (line.startsWith("-")) {
      out.push({
        text: gutter(null, "-") + line.slice(1),
        color: Theme.stateFailed,
      });
    } else if (line.startsWith("\\")) {
      out.push({
        text: gutter(null, " ") + line,
        color: Theme.textChrome,
      });
    } else if (line.startsWith(" ")) {
      out.push({
        text: gutter(newNo, " ") + line.slice(1),
        color: Theme.textSecondary,
      });
      newNo += 1;
    }
    // Anything else inside a hunk (a truncation artifact, the trailing
    // empty string from the final newline split) is not diff content.
  }
  return out;
}

/**
 * ONE scrollable view for the whole filter: per-file header rows
 * (path + diffstat, visually distinct) with the file's hunks under
 * them, concatenated. `sections` carries each file's offset so n/p can
 * jump and the verbs know which file the viewport is in. Raw unified
 * header noise (diff --git/index/---/+++) is dropped — the styled
 * header row replaces it.
 */
export function buildReviewView(
  files: ReviewFile[],
  linesForFile: (index: number) => DiffViewLine[],
): { lines: DiffViewLine[]; sections: UnifiedSection[] } {
  const lines: DiffViewLine[] = [];
  const sections: UnifiedSection[] = [];
  files.forEach((file, index) => {
    if (index > 0) lines.push({ text: "", color: Theme.textChrome });
    sections.push({ path: file.path, offset: lines.length });
    const stats = [
      file.additions !== null ? `+${file.additions}` : null,
      file.deletions !== null ? `−${file.deletions}` : null,
    ]
      .filter(Boolean)
      .join(" ");
    lines.push({
      text: `▌ ${changeGlyph(file.change)} ${file.path}${stats ? `  ${stats}` : ""}`,
      color: changeColor(file.change),
      bold: true,
    });
    for (const line of linesForFile(index)) {
      if (NOISE_PREFIXES.some((prefix) => line.text.startsWith(prefix))) {
        continue;
      }
      lines.push(line);
    }
  });
  return { lines, sections };
}
