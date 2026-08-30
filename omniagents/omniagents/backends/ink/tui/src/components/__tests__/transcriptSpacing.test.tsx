/**
 * The scroll region's vertical rhythm.
 *
 * App's scroll region declares one rule — `gap={1}` between the
 * transcript, the Preamble, "Thinking…" and the approval surfaces — and
 * that container is meant to be the only thing spacing them. Two
 * separate things quietly broke it:
 *
 *   - every transcript row carried `paddingBottom={1}`, including the
 *     last, where it separates the row from nothing. It stacked with the
 *     container's gap and made the seam below the transcript 2 rows
 *     while every other seam was 1.
 *   - an always-rendered wrapper Box for dock panels the call site
 *     stopped passing. A zero-height flex item still takes the parent's
 *     gap on BOTH sides, so it inserted 2 blank rows wherever it sat.
 *
 * Neither was written down as a number anywhere; both were emergent
 * sums. This asserts the seams directly, in a real Ink render.
 */

import React from "react";
import stripAnsi from "strip-ansi";
import { PassThrough } from "node:stream";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 100;
  rows = 60;
  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (c) => {
      if (typeof c === "string") this.frames.push(c);
    });
  }
  lastFrame(): string {
    for (let i = this.frames.length - 1; i >= 0; i--) {
      const f = stripAnsi(this.frames[i] ?? "");
      if (f.trim()) return f;
    }
    return "";
  }
}

/** Blank rows strictly between the line containing `a` and the next line
 *  containing `b`. */
function blanksBetween(frame: string, a: string, b: string): number {
  const lines = frame.split("\n").map((l) => l.trim());
  const start = lines.findIndex((l) => l.includes(a));
  if (start < 0) throw new Error(`marker not painted: ${a}`);
  const end = lines.findIndex((l, i) => i > start && l.includes(b));
  if (end < 0) throw new Error(`marker not painted: ${b}`);
  return lines.slice(start + 1, end).filter((l) => l === "").length;
}

describe("scroll-region rhythm", () => {
  let render: (typeof import("ink"))["render"];
  let Box: (typeof import("ink"))["Box"];
  let Text: (typeof import("ink"))["Text"];
  let MessageListOptimized: React.ComponentType;
  let useAppStore: (typeof import("../../store/appStore.js"))["useAppStore"];
  let instance: { unmount: () => void } | null = null;

  beforeAll(async () => {
    const ink = await import("ink");
    render = ink.render;
    Box = ink.Box;
    Text = ink.Text;
    ({ MessageListOptimized } = await import("../MessageListOptimized.js"));
    ({ useAppStore } = await import("../../store/appStore.js"));
  });

  afterEach(() => {
    instance?.unmount();
    instance = null;
  });

  const frame = async () => {
    useAppStore.getState().actions.setMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ] as never);

    const stdout = new TestStdout();
    // Mirrors App's scroll region (ScrollableMessageList): the same
    // gap={1} paddingTop={1} column, with stand-ins for the live blocks.
    instance = render(
      <Box flexDirection="column" gap={1} paddingTop={1}>
        <MessageListOptimized />
        <Text>PREAMBLE-LINE</Text>
        <Text>STATUS-THINKING</Text>
        <Text>APPROVAL-DIALOG</Text>
      </Box>,
      {
        stdout: stdout as unknown as NodeJS.WriteStream,
        debug: true,
        exitOnCtrlC: false,
        patchConsole: false,
      },
    );
    await new Promise((r) => setTimeout(r, 60));
    return stdout.lastFrame();
  };

  it("puts exactly one blank row between messages", async () => {
    expect(blanksBetween(await frame(), "hi", "hello")).toBe(1);
  });

  it("does not double the gap where the transcript meets what follows", async () => {
    // Was 2: the last row's own paddingBottom plus the container's gap.
    // The Preamble is the transcript's continuation, so it is what sits
    // here — "Thinking…" moved below it.
    expect(blanksBetween(await frame(), "hello", "PREAMBLE-LINE")).toBe(1);
  });

  it("holds the same rhythm across every following seam", async () => {
    const painted = await frame();
    expect(blanksBetween(painted, "PREAMBLE-LINE", "STATUS-THINKING")).toBe(1);
    // Was 2 whenever the empty dock-panel wrapper sat in between.
    expect(blanksBetween(painted, "STATUS-THINKING", "APPROVAL-DIALOG")).toBe(
      1,
    );
  });

  /**
   * Nothing breaks out of an activity span, so a run that reads, gets a
   * call denied and hits an error is ONE row — with the failures toned
   * onto it. The regression this guards is the opposite: a review or an
   * error splitting the span into three blocks with blank lines between.
   */
  const activityFrame = async () => {
    const tool = (id: string, extra: Record<string, unknown> = {}) => ({
      role: "tool_call",
      content: "",
      toolCall: {
        tool: "read_file",
        input: "{}",
        call_id: id,
        running: false,
        ...extra,
      },
    });
    useAppStore.getState().actions.setMessages([
      { role: "assistant", content: "BEFORE" },
      tool("a"),
      {
        role: "review",
        content: "",
        review: {
          kind: "guardian",
          blocking: true,
          line: "denied",
          rationale: "why",
        },
      },
      tool("b"),
      tool("c", {
        tool: "bash",
        output: "boom",
        metadata: { display_type: "error" },
      }),
      { role: "assistant", content: "AFTER" },
    ] as never);
    const stdout = new TestStdout();
    instance = render(
      <Box flexDirection="column" gap={1} paddingTop={1}>
        <MessageListOptimized />
      </Box>,
      {
        stdout: stdout as unknown as NodeJS.WriteStream,
        debug: true,
        exitOnCtrlC: false,
        patchConsole: false,
      },
    );
    await new Promise((r) => setTimeout(r, 60));
    return stdout.lastFrame();
  };

  it("folds a run's failures onto one activity row", async () => {
    const f = await activityFrame();
    expect(f).toContain("Read 2 files");
    expect(f).toContain("1 failed");
    expect(f).toContain("1 rejected");
    // One row, so exactly one blank line to the prose on either side.
    expect(blanksBetween(f, "BEFORE", "Read 2 files")).toBe(1);
    expect(blanksBetween(f, "Read 2 files", "AFTER")).toBe(1);
  });
});
