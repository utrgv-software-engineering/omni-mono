// Presentation logic for the `@` file-mention popup, kept out of the
// component so it is unit-testable without an Ink render.

import type { FsListEntry } from "../rpc/client.js";

export type MentionRow = {
  /** Workspace-relative path as the composer will receive it. */
  path: string;
  type: "file" | "directory";
};

const IGNORED =
  /(?:^|\/)(?:node_modules|\.git|dist|build|\.venv|__pycache__|target)(?:\/|$)/;

/** Normalise an fs_list path: strip a leading `./` and any trailing `/`. */
export function normalizeMentionPath(path: string): string {
  return path.replace(/^\.\//, "").replace(/\/+$/, "");
}

/**
 * Turn a recursive `fs_list` into candidate rows. Vendored and generated
 * trees are dropped: nobody @-mentions node_modules, and a popup that
 * lists ten thousand of its files buries the file they meant.
 */
export function buildMentionRows(
  entries: readonly FsListEntry[],
): MentionRow[] {
  const rows: MentionRow[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    const path = normalizeMentionPath(entry.path);
    if (!path || seen.has(path) || IGNORED.test(path)) continue;
    seen.add(path);
    rows.push({ path, type: entry.type });
  }
  return rows;
}

/**
 * Subsequence match in order, with a score that prefers matches in the
 * basename, at path-segment starts, and contiguous runs. Returns null
 * when the query does not match.
 */
export function mentionScore(path: string, query: string): number | null {
  const needle = query.toLowerCase();
  if (needle === "") return 0;
  const hay = path.toLowerCase();
  const slash = hay.lastIndexOf("/");
  let score = 0;
  let index = 0;
  let previous = -2;
  for (const char of needle) {
    const found = hay.indexOf(char, index);
    if (found === -1) return null;
    if (found > slash) score += 3; // in the basename
    if (found === 0 || hay[found - 1] === "/" || hay[found - 1] === ".")
      score += 2; // segment start
    if (found === previous + 1) score += 1; // contiguous
    previous = found;
    index = found + 1;
  }
  // Shorter paths win ties: `src/app.ts` over `src/app/app.test.ts`.
  return score * 100 - hay.length;
}

export function filterMentions(
  rows: readonly MentionRow[],
  query: string,
  limit = 8,
): MentionRow[] {
  const scored: Array<{ row: MentionRow; score: number }> = [];
  for (const row of rows) {
    const score = mentionScore(row.path, query);
    // A file is what an @ mention usually means; a directory of the same
    // name still lists, one row down.
    if (score !== null)
      scored.push({ row, score: score + (row.type === "file" ? 50 : 0) });
  }
  scored.sort(
    (a, b) => b.score - a.score || a.row.path.localeCompare(b.row.path),
  );
  return scored.slice(0, limit).map((entry) => entry.row);
}

/** The text that replaces the `@query` token when a row is chosen. */
export function mentionCompletion(row: MentionRow): string {
  return row.type === "directory" ? `@${row.path}/` : `@${row.path} `;
}
