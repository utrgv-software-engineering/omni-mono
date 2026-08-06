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

export function formatSize(size: number | null): string {
  if (size === null) return "";
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}K`;
  return `${(size / (1024 * 1024)).toFixed(1)}M`;
}
