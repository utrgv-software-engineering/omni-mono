// Presentation logic for the session manager, kept out of the component
// so it is unit-testable without an Ink render (same split as
// ./model-picker-model and ./task-model).
//
// The manager reads from two different server surfaces and must render
// both identically:
//
//   * ``list_threads`` / ``search_threads`` — the organized surface
//     (titles, pinned, status, fork parentage, live attention state).
//     Experimental: granted at the handshake, so it can be absent.
//   * ``list_sessions`` — the stable surface every server has.
//
// Normalizing both into one ``SessionRow`` here is what lets the
// component stay a renderer: it never asks which endpoint it is looking
// at, and the degraded path differs only in which capability flags come
// back false.

/** One conversation as the manager renders it, from either surface. */
export type SessionRow = {
  id: string;
  /** Server-set title, else the first/last message preview, else null. */
  title: string | null;
  /** Seconds since epoch. 0 when the surface reported nothing usable. */
  updatedAt: number;
  messageCount: number;
  pinned: boolean;
  archived: boolean;
  /** Set when this thread was created by ``fork_session``. */
  parentThreadId: string | null;
  /** Loaded in the server process right now. */
  active: boolean;
  /** Approvals / elicitations this thread is blocked on. */
  pendingAttention: number;
  /** Search-result excerpt around the match; null outside search. */
  preview: string | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

/** First line, collapsed and ellipsized to `max` display columns. */
export function truncate(text: string, max: number): string {
  const firstLine = text.replace(/\s+/g, " ").trim();
  if (firstLine.length <= max) return firstLine;
  return firstLine.slice(0, Math.max(0, max - 1)) + "…";
}

/** Normalize one ``list_threads`` thread payload. */
export function rowFromThread(thread: Record<string, unknown>): SessionRow {
  return {
    id: String(thread.thread_id ?? ""),
    title: str(thread.title),
    updatedAt: num(thread.updated_at) || num(thread.created_at),
    messageCount: num(thread.item_count),
    pinned: bool(thread.pinned),
    archived: thread.status === "archived",
    parentThreadId: str(thread.parent_thread_id),
    active: bool(thread.active),
    pendingAttention: num(thread.pending_attention),
    preview: null,
  };
}

/** Rows from a ``list_threads`` result, in the server's order. */
export function rowsFromThreads(result: unknown): SessionRow[] {
  const threads = (result as { threads?: unknown })?.threads;
  if (!Array.isArray(threads)) return [];
  return threads
    .filter(
      (t): t is Record<string, unknown> => typeof t === "object" && t !== null,
    )
    .map(rowFromThread)
    .filter((row) => row.id !== "");
}

/**
 * Rows from a ``search_threads`` result. Rank order is the server's and
 * is preserved — a relevance ranking that gets re-sorted client-side is
 * not a ranking — so callers must NOT pass these through `sortRows`.
 */
export function rowsFromSearch(result: unknown): SessionRow[] {
  const results = (result as { results?: unknown })?.results;
  if (!Array.isArray(results)) return [];
  const rows: SessionRow[] = [];
  for (const entry of results) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const thread = record.thread;
    if (!thread || typeof thread !== "object") continue;
    const row = rowFromThread(thread as Record<string, unknown>);
    if (row.id === "") continue;
    row.preview = str(record.preview);
    rows.push(row);
  }
  return rows;
}

/**
 * Pinned first, then most-recently-updated. Stable within a group so a
 * refetch never reshuffles rows that compare equal — the cursor is an
 * index, and a list that reorders under it moves the user's selection.
 */
export function sortRows(rows: SessionRow[]): SessionRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      if (a.row.pinned !== b.row.pinned) return a.row.pinned ? -1 : 1;
      if (a.row.updatedAt !== b.row.updatedAt) {
        return b.row.updatedAt - a.row.updatedAt;
      }
      return a.index - b.index;
    })
    .map(({ row }) => row);
}

/**
 * Client-side filter for the degraded path, where there is no
 * ``search_threads`` to delegate to. Matches title and id,
 * case-insensitively. The granted path never calls this — server-side
 * search reads transcript bodies this cannot see, and quietly
 * substituting a title match for a full-text one would look like the
 * search silently missing hits.
 */
export function filterRows(rows: SessionRow[], query: string): SessionRow[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return rows;
  return rows.filter(
    (row) =>
      (row.title ?? "").toLowerCase().includes(needle) ||
      row.id.toLowerCase().includes(needle),
  );
}

/** Short relative age, matching the status bar's token vocabulary. */
export function relativeTime(seconds: number, nowMs: number): string {
  if (seconds <= 0) return "";
  const deltaSec = Math.max(0, nowMs / 1000 - seconds);
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  if (deltaSec < 86400 * 30) return `${Math.floor(deltaSec / 86400)}d ago`;
  return new Date(seconds * 1000).toISOString().slice(0, 10);
}

/** Display label for a row: title, else a short id. */
export function rowTitle(row: SessionRow): string {
  if (row.title) return row.title;
  return row.id.length > 8 ? `${row.id.slice(0, 8)}…` : row.id;
}

/**
 * The trailing metadata line: age · message count · flags. Assembled
 * here so the row renderer stays a single `<Text>` per fact and the
 * ordering is testable.
 */
export function rowMeta(row: SessionRow, nowMs: number): string {
  const parts: string[] = [];
  const age = relativeTime(row.updatedAt, nowMs);
  if (age) parts.push(age);
  if (row.messageCount > 0) {
    parts.push(`${row.messageCount} msg${row.messageCount === 1 ? "" : "s"}`);
  }
  if (row.parentThreadId) parts.push("fork");
  if (row.archived) parts.push("archived");
  return parts.join(" · ");
}

/** Cursor index clamped into `rows` after a refetch changed its length. */
export function clampIndex(index: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(index, total - 1));
}
