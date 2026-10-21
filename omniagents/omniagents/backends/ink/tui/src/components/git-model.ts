// Presentation logic for the git panel, kept out of the component so it is
// unit-testable without an Ink render (same split as ./task-model).

import { JSONRPCError } from "../rpc/client.js";
import type {
  GitBranch,
  GitConflict,
  GitDiffFile,
  GitStatusEntry,
  GitStatusResult,
} from "../rpc/client.js";
import { Theme } from "../theme.js";

/** A parsed `-32093 git_confirmation_required` rejection. */
export type GitConfirmation = {
  /** Single-use token the retried operation must echo verbatim. */
  token: string;
  /** Paths the operation would irreversibly touch, when the server says. */
  paths: string[];
};

/**
 * Parse a `-32093 git_confirmation_required` rejection, or null for any
 * other error. Guarded operations must be re-sent echoing exactly the
 * token; anything else is a real failure. The impact paths feed the
 * confirmation prompt so the user sees what dies before pressing y.
 */
export function confirmationOf(error: unknown): GitConfirmation | null {
  if (!(error instanceof JSONRPCError) || error.code !== -32093) return null;
  const data = error.data as
    | { confirmation_token?: unknown; impact?: { paths?: unknown } }
    | undefined;
  if (typeof data?.confirmation_token !== "string") return null;
  const rawPaths = data.impact?.paths;
  const paths = Array.isArray(rawPaths)
    ? rawPaths.filter((p): p is string => typeof p === "string")
    : [];
  return { token: data.confirmation_token, paths };
}

/**
 * Branches in switcher order: current first, then local branches by name,
 * then remote-tracking refs. Worker-category branches (auto-worktrees)
 * sink below their peers within each group.
 */
export function orderBranches(branches: GitBranch[]): GitBranch[] {
  const rank = (b: GitBranch): number =>
    (b.current ? 0 : b.remote ? 4 : 2) + (b.category === "worker" ? 1 : 0);
  return [...branches].sort(
    (a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name),
  );
}

/** One selectable row in the status pane. */
export type GitFileRow = {
  path: string;
  /** Two-char porcelain XY code (`??` for untracked, `UU`-style for
   * conflicts) — rendered verbatim; it is the truthful compact form. */
  xy: string;
  staged: boolean;
  unstaged: boolean;
  conflicted: boolean;
  untracked: boolean;
  renamedFrom: string | null;
};

/**
 * Project a git_status result onto selectable rows: conflicts first (the
 * rows that block everything else), then tracked changes in server order,
 * then untracked files.
 */
export function buildFileRows(status: GitStatusResult): GitFileRow[] {
  const conflictSet = new Set(status.conflicted);
  const conflicted: GitFileRow[] = [];
  const tracked: GitFileRow[] = [];
  for (const entry of status.entries) {
    const row = entryRow(entry, conflictSet.has(entry.path));
    (row.conflicted ? conflicted : tracked).push(row);
  }
  const untracked: GitFileRow[] = status.untracked.map((path) => ({
    path,
    xy: "??",
    staged: false,
    unstaged: true,
    conflicted: false,
    untracked: true,
    renamedFrom: null,
  }));
  return [...conflicted, ...tracked, ...untracked];
}

function entryRow(entry: GitStatusEntry, conflicted: boolean): GitFileRow {
  return {
    path: entry.path,
    xy: entry.xy,
    staged: entry.staged,
    unstaged: entry.unstaged,
    conflicted: conflicted || entry.unmerged !== null,
    untracked: false,
    renamedFrom: entry.orig_path,
  };
}

export function rowColor(row: GitFileRow): string {
  if (row.conflicted) return Theme.stateFailed;
  if (row.untracked) return Theme.textChrome;
  if (row.staged && !row.unstaged) return Theme.stateDone;
  return Theme.stateActive;
}

/** Head line: branch (or short detached oid), upstream drift, repo state. */
export function headline(status: GitStatusResult): string {
  const head = status.head.unborn
    ? "(unborn)"
    : status.head.detached
      ? `detached @ ${status.head.oid?.slice(0, 8) ?? "?"}`
      : (status.head.branch ?? "?");
  const parts = [head];
  if (status.upstream) {
    const drift: string[] = [];
    if (status.upstream.ahead > 0) drift.push(`↑${status.upstream.ahead}`);
    if (status.upstream.behind > 0) drift.push(`↓${status.upstream.behind}`);
    parts.push(
      drift.length > 0
        ? `${status.upstream.name} ${drift.join(" ")}`
        : status.upstream.name,
    );
  }
  if (status.state !== "clean") parts.push(status.state.replace("_", " "));
  if (status.stash_count > 0)
    parts.push(
      `${status.stash_count} stash${status.stash_count > 1 ? "es" : ""}`,
    );
  return parts.join(" · ");
}

export type GitPanelCounts = {
  staged: number;
  unstaged: number;
  untracked: number;
  conflicted: number;
};

export function fileCounts(rows: GitFileRow[]): GitPanelCounts {
  const counts: GitPanelCounts = {
    staged: 0,
    unstaged: 0,
    untracked: 0,
    conflicted: 0,
  };
  for (const row of rows) {
    if (row.conflicted) counts.conflicted += 1;
    else if (row.untracked) counts.untracked += 1;
    else {
      if (row.staged) counts.staged += 1;
      if (row.unstaged) counts.unstaged += 1;
    }
  }
  return counts;
}

/** One renderable diff line with its display color resolved. */
export type DiffViewLine = {
  text: string;
  color: string;
  bold?: boolean;
};

/**
 * Flatten a structured diff file into display lines. `origin` is
 * structural (never a rendered prefix), so the +/-/space gutter is added
 * here exactly once.
 */
export function flattenDiff(file: GitDiffFile): DiffViewLine[] {
  const out: DiffViewLine[] = [];
  if (file.binary) {
    out.push({ text: "(binary file)", color: Theme.stateBlocked });
    return out;
  }
  if (file.submodule) {
    out.push({ text: "(submodule)", color: Theme.stateBlocked });
    return out;
  }
  for (const hunk of file.hunks) {
    out.push({
      text: hunk.section_heading
        ? `${hunk.header} ${hunk.section_heading}`
        : hunk.header,
      color: Theme.stateActive,
      bold: true,
    });
    for (const line of hunk.lines) {
      switch (line.origin) {
        case "add":
          out.push({ text: `+${line.content}`, color: Theme.stateDone });
          break;
        case "delete":
          out.push({ text: `-${line.content}`, color: Theme.stateFailed });
          break;
        case "no_newline":
          out.push({
            text: `\\ ${line.content || "No newline at end of file"}`,
            color: Theme.textChrome,
          });
          break;
        default:
          out.push({
            text: ` ${line.content}`,
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
 * Flatten conflict regions into display lines, mirroring git's marker
 * layout: ours in green, base (diff3) neutral, theirs in red, markers
 * bold. `regions_available: false` means the server could not parse
 * regions (binary/oversize) — reported, not invented.
 */
export function flattenConflict(conflict: GitConflict): DiffViewLine[] {
  if (!conflict.regions_available) {
    return [
      {
        text: "(conflict regions unavailable — binary or too large)",
        color: Theme.stateBlocked,
      },
    ];
  }
  const out: DiffViewLine[] = [];
  for (const region of conflict.regions) {
    out.push({
      text: `@@ lines ${region.start_line}–${region.end_line}`,
      color: Theme.stateActive,
      bold: true,
    });
    out.push({
      text: `<<<<<<< ${region.ours_label}`,
      color: Theme.stateActive,
      bold: true,
    });
    for (const line of region.ours) {
      out.push({ text: line, color: Theme.stateDone });
    }
    if (region.base !== null) {
      out.push({
        text: `||||||| ${region.base_label ?? "base"}`,
        color: Theme.stateActive,
        bold: true,
      });
      for (const line of region.base) {
        out.push({ text: line, color: Theme.textChrome });
      }
    }
    out.push({ text: "=======", color: Theme.stateActive, bold: true });
    for (const line of region.theirs) {
      out.push({ text: line, color: Theme.stateFailed });
    }
    out.push({
      text: `>>>>>>> ${region.theirs_label}`,
      color: Theme.stateActive,
      bold: true,
    });
  }
  if (out.length === 0) {
    out.push({ text: "(no conflict regions)", color: Theme.textChrome });
  }
  return out;
}

/** Clamp a scroll offset into [0, max(0, total - window)]. */
export function clampScroll(
  offset: number,
  total: number,
  window: number,
): number {
  return Math.max(0, Math.min(offset, Math.max(0, total - window)));
}
