import { describe, expect, it } from "vitest";
import {
  beatsLine,
  classifyToolCall,
  failureNote,
  groupTranscript,
  isErrorToolCall,
  rendersNothing,
  runningLabel,
  salientArg,
  spanLine,
} from "../transcript-model.js";
import type { Message, ToolCallInfo } from "../../store/appStore.js";

function tc(overrides: Partial<ToolCallInfo>): ToolCallInfo {
  return {
    tool: "read_file",
    input: "{}",
    call_id: "c1",
    running: false,
    ...overrides,
  };
}

function toolMsg(overrides: Partial<ToolCallInfo>): Message {
  return { role: "tool_call", content: "", toolCall: tc(overrides) };
}

function reviewMsg(blocking: boolean, rationale?: string): Message {
  return {
    role: "review",
    content: "reviewed",
    review: {
      kind: "guardian",
      blocking,
      line: blocking ? "bash denied by guardian" : "bash approved by guardian",
      rationale,
    },
  };
}

describe("classifyToolCall", () => {
  it("trusts the server's display_type first", () => {
    expect(
      classifyToolCall(
        tc({ tool: "mystery", metadata: { display_type: "diff" } }),
      ),
    ).toBe("edit");
    expect(
      classifyToolCall(
        tc({ tool: "mystery", metadata: { display_type: "file_content" } }),
      ),
    ).toBe("read");
    expect(
      classifyToolCall(
        tc({ tool: "mystery", metadata: { display_type: "command" } }),
      ),
    ).toBe("command");
  });

  it("falls back to name patterns, then item-model kind", () => {
    expect(classifyToolCall(tc({ tool: "grep_search" }))).toBe("search");
    expect(classifyToolCall(tc({ tool: "execute_bash" }))).toBe("command");
    expect(classifyToolCall(tc({ tool: "web_fetch" }))).toBe("web");
    expect(classifyToolCall(tc({ tool: "mystery", kind: "mcp" }))).toBe("mcp");
    expect(classifyToolCall(tc({ tool: "mystery" }))).toBe("other");
  });
});

describe("isErrorToolCall", () => {
  it("flags error display_type and non-zero exits", () => {
    expect(isErrorToolCall(tc({ metadata: { display_type: "error" } }))).toBe(
      true,
    );
    expect(
      isErrorToolCall(
        tc({ metadata: { display_type: "command", exit_code: 1 } }),
      ),
    ).toBe(true);
    expect(
      isErrorToolCall(
        tc({ metadata: { display_type: "command", exit_code: 0 } }),
      ),
    ).toBe(false);
    expect(isErrorToolCall(tc({}))).toBe(false);
  });
});

describe("salientArg", () => {
  it("prefers the most salient key and squashes whitespace", () => {
    expect(salientArg('{"path": "src/app.ts", "limit": 5}')).toBe("src/app.ts");
    expect(salientArg('{"command": "npm  \\n test"}')).toBe("npm test");
    expect(salientArg('{"whatever": "value"}')).toBe("value");
    expect(salientArg('{"n": 3}')).toBeNull();
    expect(salientArg("not json")).toBeNull();
  });

  it("truncates long values", () => {
    const long = salientArg(JSON.stringify({ command: "x".repeat(100) }), 20);
    expect(long).toHaveLength(20);
    expect(long!.endsWith("…")).toBe(true);
  });
});

describe("groupTranscript", () => {
  it("totals calls per verb across the span and flushes on prose", () => {
    const rows = groupTranscript([
      { role: "user", content: "go" },
      toolMsg({ call_id: "a", tool: "read_file" }),
      toolMsg({ call_id: "b", tool: "read_file" }),
      toolMsg({ call_id: "c", tool: "edit_file" }),
      { role: "assistant", content: "done reading" },
      toolMsg({ call_id: "d", tool: "read_file" }),
    ]);
    expect(rows.map((r) => r.type)).toEqual([
      "message",
      "beats",
      "message",
      "beats",
    ]);
    const first = rows[1] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(first.segments).toEqual([
      { verb: "read", count: 2 },
      { verb: "edit", count: 1 },
    ]);
    expect(beatsLine(first.segments)).toBe("Read 2 files, edited 1 file");
  });

  it("does not fragment interleaved work into count-1 stutter", () => {
    // Regression: read, <unclassified>, search, read once rendered as
    // "⌕ read 1 file · · calls 1 · ⌕ searched 1 · ⌕ read 1".
    const rows = groupTranscript([
      toolMsg({ call_id: "a", tool: "read_file" }),
      toolMsg({ call_id: "b", tool: "mystery_tool" }),
      toolMsg({ call_id: "c", tool: "grep_search" }),
      toolMsg({ call_id: "d", tool: "read_file" }),
    ]);
    expect(rows).toHaveLength(1);
    const beats = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(beats.segments).toEqual([
      { verb: "read", count: 2 },
      { verb: "other", count: 1 },
      { verb: "search", count: 1 },
    ]);
    const line = beatsLine(beats.segments);
    expect(line).toBe("Read 2 files, 1 tool call, 1 search");
  });

  it("counts errors and cancellations in the span; running calls stay in it", () => {
    const rows = groupTranscript([
      toolMsg({ call_id: "a", tool: "read_file" }),
      toolMsg({
        call_id: "b",
        tool: "execute_bash",
        metadata: { display_type: "command", exit_code: 1 },
        output: "boom",
      }),
      toolMsg({ call_id: "c", tool: "read_file", output: "[Cancelled]" }),
      toolMsg({ call_id: "d", tool: "grep", running: true }),
    ]);
    // One span: nothing breaks out any more. The two failures are a
    // toned count on it, and the in-flight call keeps it live.
    expect(rows).toHaveLength(1);
    const beats = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(beats.errors).toBe(2);
    expect(beats.segments).toEqual([{ verb: "read", count: 1 }]);
    expect(beats.running).toHaveLength(1);
  });

  it("merges landed work and the in-flight call into one live span", () => {
    const rows = groupTranscript([
      toolMsg({ call_id: "a", tool: "read_file" }),
      toolMsg({ call_id: "b", tool: "read_file" }),
      toolMsg({
        call_id: "c",
        tool: "grep_search",
        input: '{"pattern":"useRPC"}',
        running: true,
      }),
    ]);
    expect(rows).toHaveLength(1);
    const span = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(spanLine(span.segments, span.running)).toBe(
      "Read 2 files · searching useRPC…",
    );
    // Once the call lands, the same span settles into pure past tense.
    expect(
      spanLine(
        [
          { verb: "read", count: 2 },
          { verb: "search", count: 1 },
        ],
        [],
      ),
    ).toBe("Read 2 files, 1 search");
    // Nothing landed yet → the live phrase leads capitalized.
    expect(
      spanLine(
        [],
        [tc({ tool: "read_file", input: '{"path":"x"}', running: true })],
      ),
    ).toBe("Reading x…");
    // Parallel calls summarize.
    expect(
      spanLine(
        [],
        [tc({ running: true }), tc({ running: true }), tc({ running: true })],
      ),
    ).toBe("Running 3 tools…");
  });

  it("skips hidden tools and approval requests entirely", () => {
    const rows = groupTranscript([
      toolMsg({ call_id: "a", metadata: { hidden: true } }),
      { role: "approval_request", content: "" },
      { role: "system", content: "  " },
    ]);
    expect(rows).toEqual([]);
    expect(rendersNothing(toolMsg({ metadata: { hidden: true } }))).toBe(true);
  });

  it("renders system events as message rows", () => {
    const rows = groupTranscript([
      { role: "system", content: "conversation compacted" },
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.type).toBe("message");
  });
});

describe("runningLabel", () => {
  it("speaks present tense with the salient argument", () => {
    expect(
      runningLabel(tc({ tool: "read_file", input: '{"path":"src/app.ts"}' })),
    ).toEqual({ action: "reading", detail: "src/app.ts" });
    expect(
      runningLabel(
        tc({ tool: "execute_bash", input: '{"command":"npm test"}' }),
      ),
    ).toEqual({ action: "running", detail: "npm test" });
    expect(
      runningLabel(tc({ tool: "grep_search", input: '{"pattern":"useRPC"}' })),
    ).toEqual({ action: "searching", detail: "useRPC" });
  });

  it("keeps the tool name when the verb alone says nothing", () => {
    expect(runningLabel(tc({ tool: "mystery_tool", input: "{}" }))).toEqual({
      action: "running",
      detail: "mystery_tool",
    });
    expect(
      runningLabel(
        tc({ tool: "search_issues", kind: "mcp", input: '{"query":"open"}' }),
      ),
    ).toEqual({ action: "searching", detail: "open" });
  });
});

describe("reviews in the transcript", () => {
  it("folds an allowing review into the span instead of splitting it", () => {
    // The defect this guards: as a `system` message a review flushed the
    // span, so under "Approve for me" — one review per tool call — the
    // beat renderer aggregated nothing and every call got its own rows.
    const rows = groupTranscript([
      toolMsg({ call_id: "a" }),
      reviewMsg(false),
      toolMsg({ call_id: "b" }),
      reviewMsg(false),
      toolMsg({ call_id: "c" }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.type).toBe("beats");
    const beats = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(beats.segments).toEqual([{ verb: "read", count: 3 }]);
    expect(beats.reviewed).toBe(2);
  });

  it("counts folded reviews into the span line", () => {
    expect(spanLine([{ verb: "read", count: 3 }], [], 2)).toBe(
      "Read 3 files · 2 reviewed",
    );
    expect(spanLine([], [], 2)).toBe("2 reviewed");
    expect(spanLine([{ verb: "read", count: 1 }], [], 0)).toBe("Read 1 file");
  });

  it("counts a blocking review into the span rather than splitting it", () => {
    const rows = groupTranscript([
      toolMsg({ call_id: "a" }),
      reviewMsg(true, "deletes the repo"),
      toolMsg({ call_id: "b" }),
    ]);
    expect(rows).toHaveLength(1);
    const beats = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(beats.rejections).toBe(1);
    expect(beats.reviewed).toBe(0);
    expect(beats.segments).toEqual([{ verb: "read", count: 2 }]);
  });

  it("counts a failed call into the span rather than splitting it", () => {
    // A run that tries, fails and retries is ordinary agent behaviour.
    // It used to cost a broken-out row per attempt.
    const rows = groupTranscript([
      toolMsg({ call_id: "a" }),
      toolMsg({ call_id: "b", metadata: { display_type: "error" } }),
      toolMsg({ call_id: "c" }),
    ]);
    expect(rows).toHaveLength(1);
    const beats = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(beats.errors).toBe(1);
    expect(beats.segments).toEqual([{ verb: "read", count: 2 }]);
  });

  it("counts a cancelled call as a failure too", () => {
    const rows = groupTranscript([toolMsg({ output: "[Cancelled]" })]);
    const beats = rows[0] as Extract<(typeof rows)[number], { type: "beats" }>;
    expect(beats.errors).toBe(1);
  });

  it("words the toned half of the span line", () => {
    expect(failureNote(0, 0)).toBe("");
    expect(failureNote(2, 0)).toBe("2 failed");
    expect(failureNote(0, 1)).toBe("1 rejected");
    expect(failureNote(2, 1)).toBe("2 failed · 1 rejected");
  });
});
