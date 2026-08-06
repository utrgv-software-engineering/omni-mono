import { describe, expect, it } from "vitest";
import {
  buildFileRows,
  clampScroll,
  confirmationOf,
  fileCounts,
  flattenConflict,
  flattenDiff,
  headline,
  orderBranches,
} from "../git-model.js";
import type { GitBranch } from "../../rpc/client.js";
import { JSONRPCError } from "../../rpc/client.js";
import type {
  GitDiffFile,
  GitStatusEntry,
  GitStatusResult,
} from "../../rpc/client.js";

function entry(overrides: Partial<GitStatusEntry>): GitStatusEntry {
  return {
    path: "file.txt",
    orig_path: null,
    xy: " M",
    index_status: "unmodified",
    worktree_status: "modified",
    staged: false,
    unstaged: true,
    submodule: false,
    similarity: null,
    unmerged: null,
    ...overrides,
  };
}

function statusResult(overrides: Partial<GitStatusResult>): GitStatusResult {
  return {
    environment_id: "env",
    repo: ".",
    head: { detached: false, unborn: false, branch: "main", oid: "abc123" },
    upstream: null,
    entries: [],
    untracked: [],
    ignored: [],
    conflicted: [],
    stash_count: 0,
    clean: true,
    state: "clean",
    ...overrides,
  };
}

function diffFile(overrides: Partial<GitDiffFile>): GitDiffFile {
  return {
    path: "file.txt",
    old_path: null,
    change: "modified",
    old_mode: "100644",
    new_mode: "100644",
    old_oid: "a",
    new_oid: "b",
    similarity: null,
    binary: false,
    added_lines: 1,
    deleted_lines: 1,
    unmerged: false,
    submodule: false,
    hunk_selectable: true,
    hunks: [],
    ...overrides,
  };
}

describe("buildFileRows", () => {
  it("orders conflicts, then tracked changes, then untracked", () => {
    const rows = buildFileRows(
      statusResult({
        entries: [
          entry({ path: "b.txt", xy: " M" }),
          entry({
            path: "conflict.txt",
            xy: "UU",
            unmerged: {
              base_mode: "100644",
              ours_mode: "100644",
              theirs_mode: "100644",
              worktree_mode: "100644",
              base_oid: "x",
              ours_oid: "y",
              theirs_oid: "z",
            },
          }),
          entry({ path: "a.txt", xy: "M ", staged: true, unstaged: false }),
        ],
        untracked: ["new.txt"],
        conflicted: ["conflict.txt"],
      }),
    );
    expect(rows.map((r) => r.path)).toEqual([
      "conflict.txt",
      "b.txt",
      "a.txt",
      "new.txt",
    ]);
    expect(rows[0]!.conflicted).toBe(true);
    expect(rows[3]).toMatchObject({ xy: "??", untracked: true });
  });

  it("carries rename origins through", () => {
    const rows = buildFileRows(
      statusResult({
        entries: [
          entry({ path: "new-name.ts", orig_path: "old-name.ts", xy: "R " }),
        ],
      }),
    );
    expect(rows[0]!.renamedFrom).toBe("old-name.ts");
  });
});

describe("headline", () => {
  it("renders branch, drift, state and stashes", () => {
    expect(
      headline(
        statusResult({
          upstream: { name: "origin/main", ahead: 2, behind: 1 },
          state: "merging",
          stash_count: 2,
        }),
      ),
    ).toBe("main · origin/main ↑2 ↓1 · merging · 2 stashes");
  });

  it("renders a detached head by short oid", () => {
    expect(
      headline(
        statusResult({
          head: { detached: true, unborn: false, branch: null, oid: "abcdef1234" },
        }),
      ),
    ).toBe("detached @ abcdef12");
  });
});

describe("fileCounts", () => {
  it("counts each bucket once, conflicts excluded from staged/unstaged", () => {
    const rows = buildFileRows(
      statusResult({
        entries: [
          entry({ path: "a", xy: "MM", staged: true, unstaged: true }),
          entry({ path: "b", xy: "M ", staged: true, unstaged: false }),
          entry({
            path: "c",
            xy: "UU",
            unmerged: {
              base_mode: "",
              ours_mode: "",
              theirs_mode: "",
              worktree_mode: "",
              base_oid: "",
              ours_oid: "",
              theirs_oid: "",
            },
          }),
        ],
        untracked: ["d"],
        conflicted: ["c"],
      }),
    );
    expect(fileCounts(rows)).toEqual({
      staged: 2,
      unstaged: 1,
      untracked: 1,
      conflicted: 1,
    });
  });
});

describe("flattenDiff", () => {
  it("adds the gutter from structural origins", () => {
    const lines = flattenDiff(
      diffFile({
        hunks: [
          {
            hunk_id: "h1",
            index: 0,
            header: "@@ -1,2 +1,2 @@",
            section_heading: "fn main()",
            old_start: 1,
            old_lines: 2,
            new_start: 1,
            new_lines: 2,
            lines: [
              { origin: "context", content: "a", old_lineno: 1, new_lineno: 1 },
              { origin: "delete", content: "old", old_lineno: 2, new_lineno: null },
              { origin: "add", content: "new", old_lineno: null, new_lineno: 2 },
              { origin: "no_newline", content: "", old_lineno: null, new_lineno: null },
            ],
          },
        ],
      }),
    );
    expect(lines.map((l) => l.text)).toEqual([
      "@@ -1,2 +1,2 @@ fn main()",
      " a",
      "-old",
      "+new",
      "\\ No newline at end of file",
    ]);
    expect(lines[0]!.bold).toBe(true);
  });

  it("reports binary files and empty diffs without hunks", () => {
    expect(flattenDiff(diffFile({ binary: true }))[0]!.text).toBe(
      "(binary file)",
    );
    expect(flattenDiff(diffFile({}))[0]!.text).toBe("(no textual changes)");
  });
});

describe("confirmationOf", () => {
  it("extracts the token and impact paths from a typed -32093 rejection", () => {
    const error = new JSONRPCError({
      code: -32093,
      message: "discard_changes discards work and must be confirmed",
      data: {
        kind: "git_confirmation_required",
        operation: "discard_changes",
        confirmation_token: "tok-1",
        impact: { paths: ["a.txt", "b.txt"] },
      },
    });
    expect(confirmationOf(error)).toEqual({
      token: "tok-1",
      paths: ["a.txt", "b.txt"],
    });
  });

  it("tolerates a missing or non-path impact", () => {
    expect(
      confirmationOf(
        new JSONRPCError({
          code: -32093,
          message: "push rewrites remote history",
          data: {
            kind: "git_confirmation_required",
            confirmation_token: "tok-2",
            impact: { refs: ["refs/heads/main"] },
          },
        }),
      ),
    ).toEqual({ token: "tok-2", paths: [] });
  });

  it("returns null for every other error shape", () => {
    expect(confirmationOf(new Error("nope"))).toBeNull();
    expect(
      confirmationOf(new JSONRPCError({ code: -32000, message: "other" })),
    ).toBeNull();
    expect(
      confirmationOf(
        new JSONRPCError({ code: -32093, message: "no token", data: {} }),
      ),
    ).toBeNull();
  });
});

describe("orderBranches", () => {
  const branch = (overrides: Partial<GitBranch>): GitBranch => ({
    ref: `refs/heads/${overrides.name ?? "b"}`,
    name: "b",
    oid: "abc",
    remote: false,
    upstream: null,
    upstream_remote: null,
    current: false,
    worktree_path: null,
    category: "branch",
    ...overrides,
  });

  it("orders current, local, workers, then remotes", () => {
    const ordered = orderBranches([
      branch({ name: "origin/main", remote: true }),
      branch({ name: "worker/agent-1", category: "worker" }),
      branch({ name: "feature-b" }),
      branch({ name: "main", current: true }),
      branch({ name: "feature-a" }),
    ]);
    expect(ordered.map((b) => b.name)).toEqual([
      "main",
      "feature-a",
      "feature-b",
      "worker/agent-1",
      "origin/main",
    ]);
  });
});

describe("flattenConflict", () => {
  it("renders regions with git's marker layout", () => {
    const lines = flattenConflict({
      path: "x.txt",
      stages: {},
      regions_available: true,
      regions: [
        {
          start_line: 3,
          end_line: 9,
          ours_label: "HEAD",
          base_label: "base",
          theirs_label: "feature",
          ours: ["mine"],
          base: ["shared"],
          theirs: ["theirs"],
        },
      ],
    });
    expect(lines.map((l) => l.text)).toEqual([
      "@@ lines 3–9",
      "<<<<<<< HEAD",
      "mine",
      "||||||| base",
      "shared",
      "=======",
      "theirs",
      ">>>>>>> feature",
    ]);
  });

  it("reports unavailable regions instead of inventing them", () => {
    expect(
      flattenConflict({
        path: "x.bin",
        stages: {},
        regions_available: false,
        regions: [],
      })[0]!.text,
    ).toContain("unavailable");
  });
});

describe("clampScroll", () => {
  it("clamps into the scrollable range", () => {
    expect(clampScroll(-3, 100, 18)).toBe(0);
    expect(clampScroll(50, 100, 18)).toBe(50);
    expect(clampScroll(999, 100, 18)).toBe(82);
    expect(clampScroll(5, 10, 18)).toBe(0);
  });
});
