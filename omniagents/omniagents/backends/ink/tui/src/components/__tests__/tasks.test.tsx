import React from "react";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  STATUS_COLOR,
  STATUS_GLYPH,
  TASK_STATUSES,
  orderTasks,
  taskCounts,
} from "../task-model.js";
import type { TaskSummary } from "../../store/appStore.js";
import { cleanup, render } from "ink-testing-library";

const plain = (frame?: string) => stripAnsi(frame ?? "");

const task = (
  id: string,
  status: TaskSummary["status"],
  extra: Partial<TaskSummary> = {},
): TaskSummary => ({ id, subject: `task ${id}`, status, ...extra });

// ---------------------------------------------------------------------
// Ordering / counting / palette — pure, no render needed
// ---------------------------------------------------------------------

describe("task dock ordering", () => {
  it("keeps a blocked task in the dock", () => {
    // The regression this guards: `active.filter(in_progress)` ++
    // `active.filter(pending)` ++ `completed` dropped every blocked step,
    // so a plan whose last task went blocked rendered nothing and the
    // header read "0 active · 0 pending".
    const tasks = [task("1", "completed"), task("2", "blocked")];
    expect(orderTasks(tasks).map((t) => t.id)).toEqual(["2", "1"]);
  });

  it("puts active work first and finished work last", () => {
    const tasks = [
      task("1", "completed"),
      task("2", "pending"),
      task("3", "blocked"),
      task("4", "in_progress"),
    ];
    expect(orderTasks(tasks).map((t) => t.id)).toEqual(["4", "3", "2", "1"]);
  });

  it("never drops a task, whatever its status", () => {
    const tasks = TASK_STATUSES.map((status, index) =>
      task(String(index), status),
    );
    expect(orderTasks(tasks)).toHaveLength(TASK_STATUSES.length);
  });

  it("keeps a status this build does not know about", () => {
    const future = {
      id: "9",
      subject: "future",
      status: "deferred",
    } as unknown as TaskSummary;
    expect(orderTasks([task("1", "pending"), future]).map((t) => t.id)).toEqual(
      ["1", "9"],
    );
  });
});

describe("task dock counts and palette", () => {
  it("counts blocked separately from pending", () => {
    expect(
      taskCounts([
        task("1", "blocked"),
        task("2", "blocked"),
        task("3", "pending"),
      ]),
    ).toEqual({ in_progress: 0, blocked: 2, pending: 1, completed: 0 });
  });

  it("has a distinct glyph and colour for every status", () => {
    for (const status of TASK_STATUSES) {
      expect(STATUS_GLYPH[status]).toBeTruthy();
      expect(STATUS_COLOR[status]).toBeTruthy();
    }
    expect(STATUS_GLYPH.blocked).not.toBe(STATUS_GLYPH.pending);
    expect(STATUS_COLOR.blocked).not.toBe(STATUS_COLOR.pending);
  });
});

// ---------------------------------------------------------------------
// Real Ink render — the dock must actually show the blocked row
// ---------------------------------------------------------------------

let Tasks: React.ComponentType<{ focused: boolean }>;
let useAppStore: (typeof import("../../store/appStore.js"))["useAppStore"];

beforeAll(async () => {
  ({ Tasks } = await import("../Tasks.js"));
  ({ useAppStore } = await import("../../store/appStore.js"));
});

afterEach(() => {
  cleanup();
});

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const renderDock = async (tasks: TaskSummary[]) => {
  useAppStore.getState().actions.setTasks(tasks);
  useAppStore.setState({ thinking: true });
  const instance = render(<Tasks focused={true} />);
  await flush();
  return plain(instance.lastFrame());
};

describe("Tasks dock render", () => {
  it("renders a blocked task and its glyph", async () => {
    const frame = await renderDock([
      task("1", "completed"),
      task("2", "blocked", { blockedBy: ["1"] }),
    ]);
    expect(frame).toContain("task 2");
    expect(frame).toContain(STATUS_GLYPH.blocked);
    expect(frame).toContain("blocked by #1");
  });

  it("renders a blocked task with no declared blockers", async () => {
    const frame = await renderDock([task("7", "blocked")]);
    expect(frame).toContain("task 7");
    expect(frame).toContain("› blocked");
  });

  it("counts blocked work in the header", async () => {
    const frame = await renderDock([
      task("1", "blocked"),
      task("2", "pending"),
    ]);
    expect(frame).toContain("blocked");
    expect(frame).toContain("1 pending");
  });

  it("does not report a blocked-only plan as empty", async () => {
    // "0 active · 0 pending" with nothing listed was the shipped bug.
    const frame = await renderDock([task("1", "blocked")]);
    expect(frame).toContain("task 1");
  });
});
