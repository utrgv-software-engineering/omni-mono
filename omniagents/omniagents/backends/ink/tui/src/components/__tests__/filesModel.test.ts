import { describe, expect, it } from "vitest";
import {
  baseName,
  buildTree,
  formatSize,
  languageForPath,
  parentDir,
  sniffBinary,
} from "../files-model.js";
import type { FsListEntry } from "../../rpc/client.js";

const entry = (
  path: string,
  type: "file" | "directory",
  size: number | null = null,
): FsListEntry => ({ path, type, size, mtime: null });

describe("buildTree", () => {
  const listing: Record<string, FsListEntry[]> = {
    ".": [
      entry("readme.md", "file", 10),
      entry("src", "directory"),
      entry("assets", "directory"),
    ],
    src: [entry("src/app.ts", "file", 100), entry("src/lib", "directory")],
  };

  it("orders directories first, alphabetical, root collapsed", () => {
    const rows = buildTree(listing, new Set());
    expect(rows.map((r) => r.path)).toEqual(["assets", "src", "readme.md"]);
    expect(rows.every((r) => r.depth === 0)).toBe(true);
  });

  it("expands loaded directories with indented children", () => {
    const rows = buildTree(listing, new Set(["src"]));
    expect(rows.map((r) => [r.path, r.depth])).toEqual([
      ["assets", 0],
      ["src", 0],
      ["src/lib", 1],
      ["src/app.ts", 1],
      ["readme.md", 0],
    ]);
    expect(rows[1]?.expanded).toBe(true);
  });

  it("treats an expanded-but-unloaded directory as childless", () => {
    const rows = buildTree(listing, new Set(["assets"]));
    expect(rows.map((r) => r.path)).toEqual(["assets", "src", "readme.md"]);
  });
});

describe("path helpers", () => {
  it("splits names and parents", () => {
    expect(baseName("src/app.ts")).toBe("app.ts");
    expect(parentDir("src/app.ts")).toBe("src");
    expect(parentDir("top.txt")).toBe(".");
  });
});

describe("sniffBinary", () => {
  it("flags NUL bytes and passes text", () => {
    expect(sniffBinary(new Uint8Array([104, 105, 0, 33]))).toBe(true);
    expect(sniffBinary(new TextEncoder().encode("plain text\n"))).toBe(false);
  });
});

describe("languageForPath", () => {
  it("maps extensions and special names", () => {
    expect(languageForPath("src/app.ts")).toBe("typescript");
    expect(languageForPath("a/b/mod.rs")).toBe("rust");
    expect(languageForPath("Dockerfile")).toBe("dockerfile");
    expect(languageForPath("Makefile")).toBe("makefile");
    expect(languageForPath("notes.txt")).toBeNull();
    expect(languageForPath("README")).toBeNull();
  });
});

describe("formatSize", () => {
  it("humanizes byte counts", () => {
    expect(formatSize(null)).toBe("");
    expect(formatSize(512)).toBe("512B");
    expect(formatSize(2048)).toBe("2.0K");
    expect(formatSize(3 * 1024 * 1024)).toBe("3.0M");
  });
});
