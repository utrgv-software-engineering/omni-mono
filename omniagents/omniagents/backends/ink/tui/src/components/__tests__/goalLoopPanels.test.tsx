import React from "react";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { LoopTaskSnapshot } from "@omniagents/client/session/panel-types";
import { useAppStore } from "../../store/appStore.js";
import { goalActions } from "../GoalPanel.js";
import { describeSchedule, orderLoops } from "../LoopPanel.js";
import { cleanup, render } from "ink-testing-library";

const plain = (frame?: string) => stripAnsi(frame ?? "");

const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));

let GoalPanel: (typeof import("../GoalPanel.js"))["GoalPanel"];
let LoopPanel: (typeof import("../LoopPanel.js"))["LoopPanel"];
const initial = useAppStore.getState();

beforeAll(async () => {
  ({ GoalPanel } = await import("../GoalPanel.js"));
  ({ LoopPanel } = await import("../LoopPanel.js"));
});
afterEach(() => {
  cleanup();
  useAppStore.setState(initial, true);
});

function mount(node: React.ReactNode) {
  return render(<KeypressProvider>{node}</KeypressProvider>);
}

const loop = (
  id: string,
  extra: Partial<LoopTaskSnapshot> = {},
): LoopTaskSnapshot => ({
  id,
  prompt: `prompt ${id}`,
  mode: "fixed_interval",
  cron: null,
  interval_seconds: 300,
  recurring: true,
  created_at: 1,
  expires_at: null,
  next_fire_at: null,
  fires: 0,
  status: "active",
  last_fire_at: null,
  last_reason: null,
  jitter_seconds: 0,
  ...extra,
});

describe("goal panel model", () => {
  it("offers pause and stop while active, resume and stop while paused, nothing after", () => {
    const base = {
      goal: "ship it",
      turn: 1,
      max_turns: 10,
      tick_interval: 60,
      last_reason: null,
      started_at: 0,
      completion_reason: null,
    };
    expect(goalActions({ ...base, status: "active" })).toEqual([
      "pause",
      "stop",
    ]);
    expect(goalActions({ ...base, status: "paused" })).toEqual([
      "resume",
      "stop",
    ]);
    expect(goalActions({ ...base, status: "completed" })).toEqual([]);
    expect(goalActions(null)).toEqual([]);
  });
});

describe("loop panel model", () => {
  it("orders active loops by next fire, then the rest newest first", () => {
    const ordered = orderLoops([
      loop("done", { status: "completed", created_at: 5 }),
      loop("late", { next_fire_at: 200 }),
      loop("soon", { next_fire_at: 100 }),
      loop("old", { status: "cancelled", created_at: 1 }),
    ]);
    expect(ordered.map((l) => l.id)).toEqual(["soon", "late", "done", "old"]);
  });
  it("describes schedules in human units", () => {
    expect(describeSchedule(loop("a", { interval_seconds: 300 }))).toBe(
      "every 5m",
    );
    expect(describeSchedule(loop("b", { interval_seconds: 7200 }))).toBe(
      "every 2h",
    );
    expect(
      describeSchedule(loop("c", { mode: "fixed_cron", cron: "0 9 * * *" })),
    ).toBe("0 9 * * *");
    expect(
      describeSchedule(loop("d", { mode: "dynamic", interval_seconds: null })),
    ).toBe("when the agent asks");
  });
});

describe("panels", () => {
  it("goal: s stops through the callback and esc closes", async () => {
    useAppStore.getState().actions.setGoal({
      goal: "ship it",
      turn: 2,
      max_turns: 10,
      tick_interval: 60,
      last_reason: "ran tests",
      started_at: Math.floor(Date.now() / 1000),
      completion_reason: null,
      status: "active",
    });
    const onAction = vi.fn(async () => ({ message: "stopped" }));
    const onUnfocus = vi.fn();
    const { stdin, lastFrame } = mount(
      <GoalPanel focused onUnfocus={onUnfocus} onAction={onAction} />,
    );
    await settle();
    expect(plain(lastFrame())).toContain("ship it");
    expect(plain(lastFrame())).toContain("p pause");
    stdin.write("s");
    await settle();
    expect(onAction).toHaveBeenCalledWith("stop");
    stdin.write("");
    await settle(120);
    expect(onUnfocus).toHaveBeenCalled();
  });

  it("loop: x stops the selected loop and c clears", async () => {
    useAppStore
      .getState()
      .actions.setLoops([
        loop("l1", { next_fire_at: 10 }),
        loop("l2", { next_fire_at: 20 }),
      ]);
    const onStop = vi.fn(async () => ({}));
    const onClear = vi.fn(async () => ({}));
    const { stdin, lastFrame } = mount(
      <LoopPanel
        focused
        onUnfocus={() => undefined}
        onStop={onStop}
        onClear={onClear}
      />,
    );
    await settle();
    expect(plain(lastFrame())).toContain("prompt l1");
    stdin.write("[B");
    await settle();
    stdin.write("x");
    await settle();
    expect(onStop).toHaveBeenCalledWith("l2");
    stdin.write("c");
    await settle();
    expect(onClear).toHaveBeenCalled();
  });

  it("renders a hint, not nothing, when there is no goal", async () => {
    const { lastFrame } = mount(
      <GoalPanel
        focused
        onUnfocus={() => undefined}
        onAction={async () => ({})}
      />,
    );
    await settle();
    expect(plain(lastFrame())).toContain("/goal <text> starts one");
  });
});
