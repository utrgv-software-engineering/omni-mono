import { describe, expect, it } from "vitest";
import type { Message } from "../../store/appStore.js";
import {
  exportFileName,
  lastAssistantText,
  mcpLines,
  osc52,
  statusLines,
  transcriptToMarkdown,
} from "../session-commands-model.js";

const messages: Message[] = [
  { role: "user", content: "hi" },
  {
    role: "tool_call",
    content: "ok",
    toolCall: {
      tool: "read_file",
      input: '{"path":"a"}',
      call_id: "c",
      running: false,
    },
  },
  { role: "assistant", content: "first" },
  { role: "assistant", content: "   " },
  { role: "command", command: "status", content: "session   abc" },
];

describe("statusLines", () => {
  it("reports the facts it has and says so when it has none", () => {
    const lines = statusLines({
      sessionId: null,
      model: null,
      usage: null,
      goal: null,
      loops: [],
      workspaceRoot: null,
      connected: false,
    });
    expect(lines[0]).toContain("starts on first send");
    expect(lines).toContain("tokens    none yet");
    expect(lines.at(-1)).toBe("connection disconnected");
  });
  it("formats tokens compactly and includes a live goal", () => {
    const lines = statusLines({
      sessionId: "abc",
      model: "gpt-5",
      usage: {
        inputTokens: 12000,
        outputTokens: 800,
        totalTokens: 12800,
        maxInputTokens: 200000,
      },
      goal: {
        goal: "ship",
        status: "active",
        turn: 2,
        max_turns: 9,
        tick_interval: 60,
        last_reason: null,
        started_at: 0,
        completion_reason: null,
      },
      loops: [],
      workspaceRoot: "/w",
      connected: true,
    });
    expect(lines).toContain(
      "tokens    12.8k total · 12.0k in · 800 out of 200.0k context",
    );
    expect(lines.some((l) => l.startsWith("goal      active · turn 2/9"))).toBe(
      true,
    );
  });
});

describe("copy and export", () => {
  it("copies the last non-empty assistant message, never command output", () => {
    expect(lastAssistantText(messages)).toBe("first");
    expect(lastAssistantText([{ role: "user", content: "x" }])).toBeNull();
  });
  it("wraps text in an OSC 52 sequence", () => {
    expect(osc52("hi")).toBe("]52;c;aGk=");
  });
  it("renders the transcript as markdown with tool calls fenced", () => {
    const md = transcriptToMarkdown(messages, {
      sessionId: "s1",
      model: "m",
      exportedAt: new Date(0),
    });
    expect(md).toContain("# Conversation s1");
    expect(md).toContain("## User\n\nhi");
    expect(md).toContain("### Tool: read_file");
    expect(md).toContain('```json\n{"path":"a"}\n```');
    expect(md).toContain("## Assistant\n\nfirst");
    expect(md).toContain("### /status\n\n```\nsession   abc\n```");
  });
  it("names the export file by session and time", () => {
    expect(
      exportFileName("abcdefghijk", new Date("2026-09-10T12:34:56.000Z")),
    ).toBe("omni-session-abcdefgh-2026-09-10T12-34-56.md");
    expect(exportFileName(null, new Date(0))).toMatch(/^omni-session-draft-/);
  });
});

describe("mcp listing", () => {
  it("summarises servers one per line", () => {
    expect(
      mcpLines([
        {
          server_name: "fs",
          enabled: true,
          status: "connected",
          counts: { tools: 4 },
        },
        {
          server_name: "gh",
          enabled: false,
          disabled_reason: "allowlist_blocked",
        },
      ]),
    ).toEqual([
      "fs · connected · 4 tools",
      "gh · disabled (allowlist_blocked)",
    ]);
    expect(mcpLines([])).toEqual(["No MCP servers configured."]);
  });
});
