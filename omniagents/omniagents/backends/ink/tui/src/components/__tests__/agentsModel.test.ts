import { describe, expect, it } from "vitest";
import {
  buildLiveActivity,
  buildWorkerDetail,
  wrapPlain,
} from "../agents-model.js";
import type { SubagentSummary } from "../../store/appStore.js";

describe("wrapPlain", () => {
  it("breaks on spaces and preserves explicit newlines", () => {
    expect(wrapPlain("one two three four", 9)).toEqual([
      "one two",
      "three",
      "four",
    ]);
    expect(wrapPlain("a\nb", 40)).toEqual(["a", "b"]);
  });

  it("hard-breaks tokens longer than the width", () => {
    expect(wrapPlain("abcdefghijkl", 8)).toEqual(["abcdefgh", "ijkl"]);
  });
});

const worker = (overrides: Partial<SubagentSummary>): SubagentSummary =>
  ({
    subagent_id: "w1",
    kind: "worker",
    task: "write a story",
    status: "completed",
    result: null,
    error: null,
    isolation: null,
    started_at: 0,
    wall_time_ms: 1000,
    ...overrides,
  }) as SubagentSummary;

describe("buildWorkerDetail", () => {
  it("wraps the task and body, with a meta line between", () => {
    const lines = buildWorkerDetail(
      worker({ result: "done well and truly finished" }),
      12,
    );
    expect(lines[0]).toMatchObject({ text: "write a", bold: true });
    expect(lines.map((l) => l.text)).toContain("completed · w1");
    // Markdown rendering may wrap the body rows in ANSI styling, so
    // assert containment, not equality.
    expect(lines.some((l) => l.text.includes("done well"))).toBe(true);
  });

  it("renders result markdown instead of raw markers", () => {
    const lines = buildWorkerDetail(
      worker({ result: "**What was done**: everything" }),
      80,
    );
    const body = lines.map((l) => l.text).join("\n");
    expect(body).toContain("What was done");
    expect(body).not.toContain("**");
  });

  it("marks a running worker with a placeholder body", () => {
    const lines = buildWorkerDetail(
      worker({ status: "running", result: null }),
      60,
    );
    expect(lines.at(-1)?.text).toBe("working — activity streams here");
  });
});


describe("buildLiveActivity", () => {
  it("renders tool calls in beat language, prose wrapped, failures red", () => {
    const lines = buildLiveActivity(
      [
        {
          method: "tool_called",
          params: {
            tool: "read_file",
            input: JSON.stringify({ path: "src/app.ts" }),
            call_id: "c1",
          },
        },
        {
          method: "tool_result",
          params: { tool: "read_file", metadata: { success: false } },
        },
        { method: "message_output", params: { content: "found the bug" } },
        { method: "run_started", params: {} },
      ],
      60,
    );
    expect(lines.map((l) => l.text)).toEqual([
      "· reading src/app.ts",
      "✗ read_file failed",
      "found the bug",
    ]);
  });

  it("feeds the running worker's detail pane", () => {
    const lines = buildWorkerDetail(
      {
        subagent_id: "w1",
        kind: "worker",
        task: "fix it",
        status: "running",
        result: null,
        error: null,
        isolation: null,
        started_at: 0,
        wall_time_ms: 0,
      } as never,
      60,
      [{ method: "message_output", params: { content: "on it" } }],
    );
    expect(lines.at(-1)?.text).toBe("on it");
  });
});


describe("settled subagent detail", () => {
  it("keeps the action transcript and appends the result without repeating prose", () => {
    const lines = buildWorkerDetail(
      {
        subagent_id: "call-1",
        kind: "agent_tool",
        agent: "explorer",
        task: "inspect the app",
        status: "completed",
        result: "It is a small web app.",
        error: null,
        isolation: null,
        started_at: 0,
        wall_time_ms: 1000,
      } as never,
      60,
      [
        {
          method: "tool_called",
          params: {
            tool: "read_file",
            input: JSON.stringify({ path: "index.html" }),
            call_id: "c1",
          },
        },
        { method: "message_output", params: { content: "It is a small web app." } },
      ],
    );
    const texts = lines.map((l) => l.text);
    expect(texts).toContain("· reading index.html");
    // Prose appears once (the result), not twice.
    expect(
      texts.filter((t) => t.includes("It is a small web app.")).length,
    ).toBe(1);
  });
});
