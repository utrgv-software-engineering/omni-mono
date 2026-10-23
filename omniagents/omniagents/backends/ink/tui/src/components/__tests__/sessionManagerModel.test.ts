import { describe, expect, it } from "vitest";
import {
  clampIndex,
  filterRows,
  relativeTime,
  rowMeta,
  rowsFromSearch,
  rowsFromThreads,
  rowTitle,
  sortRows,
  truncate,
  type SessionRow,
} from "../session-manager-model.js";

function thread(overrides: Record<string, unknown> = {}) {
  return {
    thread_id: "t-1",
    title: "Refactor the loader",
    status: "active",
    created_at: 1_700_000_000,
    updated_at: 1_700_000_500,
    item_count: 12,
    turn_count: 4,
    parent_thread_id: null,
    pinned: false,
    active: false,
    pending_attention: 0,
    ...overrides,
  };
}

function row(overrides: Partial<SessionRow> = {}): SessionRow {
  return {
    id: "t-1",
    title: "Refactor the loader",
    updatedAt: 1_700_000_500,
    messageCount: 12,
    pinned: false,
    archived: false,
    parentThreadId: null,
    active: false,
    pendingAttention: 0,
    preview: null,
    ...overrides,
  };
}

describe("rowsFromThreads", () => {
  it("normalizes a list_threads payload", () => {
    const rows = rowsFromThreads({ threads: [thread()] });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      id: "t-1",
      title: "Refactor the loader",
      updatedAt: 1_700_000_500,
      messageCount: 12,
      pinned: false,
      archived: false,
    });
  });

  it("maps archived status and fork parentage", () => {
    const rows = rowsFromThreads({
      threads: [thread({ status: "archived", parent_thread_id: "t-0" })],
    });
    expect(rows[0]!.archived).toBe(true);
    expect(rows[0]!.parentThreadId).toBe("t-0");
  });

  it("falls back to created_at when updated_at is missing", () => {
    const rows = rowsFromThreads({
      threads: [thread({ updated_at: undefined })],
    });
    expect(rows[0]!.updatedAt).toBe(1_700_000_000);
  });

  it("carries the live attention state the picker badges", () => {
    const rows = rowsFromThreads({
      threads: [thread({ active: true, pending_attention: 2 })],
    });
    expect(rows[0]!.active).toBe(true);
    expect(rows[0]!.pendingAttention).toBe(2);
  });

  it("drops rows with no thread id rather than rendering an empty one", () => {
    const rows = rowsFromThreads({
      threads: [thread(), thread({ thread_id: "" })],
    });
    expect(rows).toHaveLength(1);
  });

  it("tolerates a malformed payload", () => {
    expect(rowsFromThreads(undefined)).toEqual([]);
    expect(rowsFromThreads({})).toEqual([]);
    expect(rowsFromThreads({ threads: "nope" })).toEqual([]);
    expect(rowsFromThreads({ threads: [null, 3] })).toEqual([]);
  });
});

describe("rowsFromSearch", () => {
  it("keeps the server's rank order and attaches the preview", () => {
    const rows = rowsFromSearch({
      results: [
        {
          thread_id: "t-2",
          preview: "…loader…",
          thread: thread({ thread_id: "t-2" }),
        },
        {
          thread_id: "t-1",
          preview: "…other…",
          thread: thread({ thread_id: "t-1" }),
        },
      ],
    });
    expect(rows.map((r) => r.id)).toEqual(["t-2", "t-1"]);
    expect(rows[0]!.preview).toBe("…loader…");
  });

  it("skips a hit whose thread row vanished", () => {
    const rows = rowsFromSearch({
      results: [{ thread_id: "t-9", preview: "x" }],
    });
    expect(rows).toEqual([]);
  });
});

describe("sortRows", () => {
  it("puts pinned rows first, then newest", () => {
    const rows = sortRows([
      row({ id: "a", updatedAt: 300 }),
      row({ id: "b", updatedAt: 100, pinned: true }),
      row({ id: "c", updatedAt: 200 }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["b", "a", "c"]);
  });

  it("is stable for rows that compare equal", () => {
    const rows = sortRows([
      row({ id: "a", updatedAt: 100 }),
      row({ id: "b", updatedAt: 100 }),
      row({ id: "c", updatedAt: 100 }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("does not mutate its input", () => {
    const input = [
      row({ id: "a", updatedAt: 1 }),
      row({ id: "b", updatedAt: 2 }),
    ];
    sortRows(input);
    expect(input.map((r) => r.id)).toEqual(["a", "b"]);
  });
});

describe("filterRows", () => {
  it("matches title and id case-insensitively", () => {
    const rows = [
      row({ id: "abc", title: "Loader work" }),
      row({ id: "xyz", title: null }),
    ];
    expect(filterRows(rows, "loader").map((r) => r.id)).toEqual(["abc"]);
    expect(filterRows(rows, "XY").map((r) => r.id)).toEqual(["xyz"]);
  });

  it("returns everything for an empty query", () => {
    const rows = [row({ id: "a" }), row({ id: "b" })];
    expect(filterRows(rows, "   ")).toHaveLength(2);
  });
});

describe("relativeTime", () => {
  const now = 1_700_086_400_000; // ms

  it("renders coarse buckets", () => {
    expect(relativeTime(now / 1000 - 10, now)).toBe("just now");
    expect(relativeTime(now / 1000 - 120, now)).toBe("2m ago");
    expect(relativeTime(now / 1000 - 7200, now)).toBe("2h ago");
    expect(relativeTime(now / 1000 - 86400 * 3, now)).toBe("3d ago");
  });

  it("falls back to a date beyond a month", () => {
    expect(relativeTime(now / 1000 - 86400 * 60, now)).toMatch(
      /^\d{4}-\d{2}-\d{2}$/,
    );
  });

  it("renders nothing for an unknown timestamp", () => {
    expect(relativeTime(0, now)).toBe("");
  });
});

describe("rowTitle / rowMeta", () => {
  it("falls back to a short id when untitled", () => {
    expect(rowTitle(row({ title: null, id: "0123456789abcdef" }))).toBe(
      "01234567…",
    );
    expect(rowTitle(row({ title: "Named" }))).toBe("Named");
  });

  it("assembles metadata in a fixed order", () => {
    const now = 1_700_000_600_000;
    const meta = rowMeta(
      row({
        updatedAt: now / 1000 - 3600,
        messageCount: 1,
        parentThreadId: "p",
        archived: true,
      }),
      now,
    );
    expect(meta).toBe("1h ago · 1 msg · fork · archived");
  });

  it("omits absent facts entirely", () => {
    expect(rowMeta(row({ updatedAt: 0, messageCount: 0 }), Date.now())).toBe(
      "",
    );
  });
});

describe("truncate", () => {
  it("collapses whitespace and ellipsizes", () => {
    expect(truncate("  a\n  b  ", 40)).toBe("a b");
    expect(truncate("abcdefghij", 5)).toBe("abcd…");
  });
});

describe("clampIndex", () => {
  it("keeps the cursor inside a list that shrank", () => {
    expect(clampIndex(7, 3)).toBe(2);
    expect(clampIndex(-1, 3)).toBe(0);
    expect(clampIndex(1, 0)).toBe(0);
  });
});
