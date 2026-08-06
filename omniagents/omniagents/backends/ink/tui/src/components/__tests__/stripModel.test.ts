import { describe, expect, it } from "vitest";
import {
  buildPanelMenuRows,
  buildStripTokens,
  type StripState,
} from "../strip-model.js";

function state(overrides: Partial<StripState>): StripState {
  return {
    tasks: [],
    subagents: [],
    bashJobs: [],
    queuedMessages: [],
    notificationCount: 0,
    goal: null,
    wakeup: null,
    loops: [],
    runDiff: null,
    ...overrides,
  };
}

const task = (id: string, status: string) => ({
  id,
  subject: `t${id}`,
  status: status as never,
});

describe("buildStripTokens", () => {
  it("returns nothing when there is nothing to report", () => {
    expect(buildStripTokens(state({}))).toEqual([]);
  });

  it("summarizes tasks with a blocked attention token", () => {
    const tokens = buildStripTokens(
      state({
        tasks: [
          task("1", "completed"),
          task("2", "in_progress"),
          task("3", "blocked"),
        ],
      }),
    );
    expect(tokens).toEqual([
      { text: "tasks 1/3", tone: "info" },
      { text: "1 blocked", tone: "warn" },
    ]);
  });

  it("surfaces failed jobs and workers as warn tokens", () => {
    const tokens = buildStripTokens(
      state({
        subagents: [
          { kind: "worker", status: "running" } as never,
          { kind: "worker", status: "error" } as never,
        ],
        bashJobs: [
          { running: true, exit_code: null } as never,
          { running: false, exit_code: 1 } as never,
          { running: false, exit_code: 0 } as never,
        ],
      }),
    );
    expect(tokens).toEqual([
      { text: "1 worker", tone: "info" },
      { text: "1 worker failed", tone: "warn" },
      { text: "1 job", tone: "info" },
      { text: "1 job failed", tone: "warn" },
    ]);
  });

  it("reports queue, notes, diffstat and schedules dimly", () => {
    const tokens = buildStripTokens(
      state({
        queuedMessages: [{} as never, {} as never],
        notificationCount: 3,
        runDiff: {
          stats: { files_changed: 2, additions: 45, deletions: 12 },
        } as never,
        goal: { goal: "refactor the parser end to end", status: "active" } as never,
        wakeup: { status: "active", interval_seconds: 300 } as never,
        loops: [{ status: "active" } as never, { status: "cancelled" } as never],
      }),
    );
    expect(tokens).toEqual([
      { text: "2 queued", tone: "dim" },
      { text: "3 notes", tone: "dim" },
      { text: "goal refactor the parser end…", tone: "dim" },
      { text: "wake 5m", tone: "dim" },
      { text: "1 loop", tone: "dim" },
    ]);
  });

  it("marks a paused goal", () => {
    const tokens = buildStripTokens(
      state({ goal: { goal: "ship", status: "paused" } as never }),
    );
    expect(tokens).toEqual([{ text: "goal ship (paused)", tone: "dim" }]);
  });
});

describe("buildPanelMenuRows", () => {
  it("lists every panel with unique mnemonics even when idle", () => {
    const rows = buildPanelMenuRows(state({}));
    expect(rows.map((r) => r.panel)).toEqual([
      "tasks",
      "workers",
      "queue",
      "jobs",
      "notifications",
      "git",
      "terminal",
      "models",
    ]);
    const keys = rows.map((r) => r.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(rows.every((r) => r.summary === null && !r.warn)).toBe(true);
  });

  it("sorts attention panels first and carries live summaries", () => {
    const rows = buildPanelMenuRows(
      state({
        tasks: [task("1", "completed"), task("2", "in_progress")],
        bashJobs: [{ running: false, exit_code: 2 } as never],
        runDiff: {
          stats: { files_changed: 3, additions: 10, deletions: 4 },
        } as never,
      }),
    );
    expect(rows[0]).toMatchObject({
      panel: "jobs",
      warn: true,
      summary: "1 failed",
    });
    expect(rows.find((r) => r.panel === "tasks")?.summary).toBe("1/2");
    expect(rows.find((r) => r.panel === "git")?.summary).toBe(
      "3 files edited +10 −4",
    );
  });
});
