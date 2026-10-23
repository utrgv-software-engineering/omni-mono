import { describe, expect, it } from "vitest";
import {
  buildMentionRows,
  filterMentions,
  mentionCompletion,
  mentionScore,
} from "../mention-model.js";

const entries = [
  {
    path: "./src/app.ts",
    type: "file" as const,
    size: 1,
    mtime: null,
    writable: true,
  },
  {
    path: "src/app/",
    type: "directory" as const,
    size: null,
    mtime: null,
    writable: true,
  },
  {
    path: "src/app/app.test.ts",
    type: "file" as const,
    size: 1,
    mtime: null,
    writable: true,
  },
  {
    path: "node_modules/x/index.js",
    type: "file" as const,
    size: 1,
    mtime: null,
    writable: true,
  },
  {
    path: ".git/HEAD",
    type: "file" as const,
    size: 1,
    mtime: null,
    writable: true,
  },
  {
    path: "README.md",
    type: "file" as const,
    size: 1,
    mtime: null,
    writable: true,
  },
];

describe("buildMentionRows", () => {
  it("normalizes paths and drops vendored trees", () => {
    expect(buildMentionRows(entries).map((row) => row.path)).toEqual([
      "src/app.ts",
      "src/app",
      "src/app/app.test.ts",
      "README.md",
    ]);
  });
});

describe("filterMentions", () => {
  const rows = buildMentionRows(entries);
  it("returns everything for an empty query, capped", () => {
    expect(filterMentions(rows, "", 2)).toHaveLength(2);
  });
  it("ranks the shorter basename match first", () => {
    expect(filterMentions(rows, "app").map((row) => row.path)).toEqual([
      "src/app.ts",
      "src/app",
      "src/app/app.test.ts",
    ]);
  });
  it("matches subsequences across segments and rejects non-matches", () => {
    expect(mentionScore("src/app/app.test.ts", "sat")).not.toBeNull();
    expect(mentionScore("README.md", "zzz")).toBeNull();
    expect(filterMentions(rows, "read").map((row) => row.path)).toEqual([
      "README.md",
    ]);
  });
});

describe("mentionCompletion", () => {
  it("adds a trailing space for files and a slash for directories", () => {
    expect(mentionCompletion({ path: "src/app.ts", type: "file" })).toBe(
      "@src/app.ts ",
    );
    expect(mentionCompletion({ path: "src/app", type: "directory" })).toBe(
      "@src/app/",
    );
  });
});
