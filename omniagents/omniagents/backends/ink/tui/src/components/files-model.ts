// Presentation logic for the Files screen. Pure and unit-testable
// (same split as ./review-model / ./agents-model).

import type { FsListEntry } from "../rpc/client.js";

/** One visible row of the lazily-loaded tree. */
export type TreeRow = {
  path: string;
  name: string;
  type: "file" | "directory";
  depth: number;
  expanded: boolean;
  size: number | null;
};

export function baseName(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

export function parentDir(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).join("/") || ".";
}

const typeRank = (type: string) => (type === "directory" ? 0 : 1);

export function sortEntries(entries: FsListEntry[]): FsListEntry[] {
  return [...entries].sort(
    (a, b) =>
      typeRank(a.type) - typeRank(b.type) ||
      baseName(a.path).localeCompare(baseName(b.path)),
  );
}

/**
 * Flatten the lazily-loaded tree into visible rows: directories first,
 * alphabetical, expanded directories contributing their children (when
 * their listing has arrived — an expanded-but-unloaded directory simply
 * has no children yet).
 */
export function buildTree(
  entriesByDir: Record<string, FsListEntry[] | undefined>,
  expanded: ReadonlySet<string>,
  root = ".",
): TreeRow[] {
  const rows: TreeRow[] = [];
  const walk = (dir: string, depth: number) => {
    const entries = entriesByDir[dir];
    if (!entries) return;
    for (const entry of sortEntries(entries)) {
      const isDir = entry.type === "directory";
      const isExpanded = isDir && expanded.has(entry.path);
      rows.push({
        path: entry.path,
        name: baseName(entry.path),
        type: entry.type,
        depth,
        expanded: isExpanded,
        size: entry.size,
      });
      if (isExpanded) walk(entry.path, depth + 1);
    }
  };
  walk(root, 0);
  return rows;
}

/** NUL byte in the head = binary — the same heuristic git uses. */
export function sniffBinary(head: Uint8Array): boolean {
  const cap = Math.min(head.length, 8192);
  for (let i = 0; i < cap; i += 1) {
    if (head[i] === 0) return true;
  }
  return false;
}

const LANGUAGE_BY_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  mts: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  rs: "rust",
  go: "go",
  rb: "ruby",
  java: "java",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  hpp: "cpp",
  cs: "csharp",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  fish: "bash",
  json: "json",
  yml: "yaml",
  yaml: "yaml",
  toml: "ini",
  ini: "ini",
  xml: "xml",
  html: "xml",
  css: "css",
  scss: "scss",
  less: "less",
  md: "markdown",
  markdown: "markdown",
  sql: "sql",
  diff: "diff",
  patch: "diff",
  dockerfile: "dockerfile",
  makefile: "makefile",
  lua: "lua",
  vim: "vim",
};

/** highlight.js language for a path, or null for plain text. */
export function languageForPath(path: string): string | null {
  const name = baseName(path).toLowerCase();
  if (name === "dockerfile") return "dockerfile";
  if (name === "makefile") return "makefile";
  const dot = name.lastIndexOf(".");
  if (dot < 0) return null;
  return LANGUAGE_BY_EXT[name.slice(dot + 1)] ?? null;
}

export function formatSize(size: number | null): string {
  if (size === null) return "";
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}K`;
  return `${(size / (1024 * 1024)).toFixed(1)}M`;
}
