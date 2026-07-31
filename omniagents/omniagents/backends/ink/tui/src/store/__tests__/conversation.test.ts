import { describe, expect, it } from "vitest";

import type { ConversationItem } from "../../types/conversation.js";
import { isTerminal, mergeItems, normalizeItems, toolKind } from "../conversation.js";

function item(
  overrides: Partial<ConversationItem> &
    Pick<ConversationItem, "seq" | "kind">,
): ConversationItem {
  return {
    item_id: `itm_${overrides.seq}`,
    thread_id: "thread-1",
    turn_id: "run-1",
    status: "completed",
    role: null,
    created_at: 0,
    updated_at: 0,
    completed_at: 0,
    revision: 0,
    content: {},
    source_ref: {},
    long_lived: false,
    source: "recorded",
    schema_version: 1,
    ...overrides,
  } as ConversationItem;
}

describe("normalizeItems", () => {
  it("reconstructs a completed conversation from persisted items alone", () => {
    const messages = normalizeItems([
      item({ seq: 1, kind: "user_message", content: { text: "ship it" } }),
      item({
        seq: 2,
        kind: "tool_call",
        content: {
          tool: "write_file",
          tool_kind: "file",
          call_id: "c1",
          input: { path: "a.txt" },
          output: "written",
        },
      }),
      item({ seq: 3, kind: "agent_message", content: { text: "done" } }),
    ]);

    expect(messages).toHaveLength(3);
    expect(messages[0]).toEqual({ role: "user", content: "ship it" });
    expect(messages[1].role).toBe("tool_call");
    expect(messages[1].toolCall).toEqual({
      tool: "write_file",
      input: '{"path":"a.txt"}',
      call_id: "c1",
      output: "written",
      metadata: undefined,
      running: false,
    });
    expect(messages[2]).toEqual({ role: "assistant", content: "done" });
  });

  it("orders by seq rather than array position", () => {
    const messages = normalizeItems([
      item({ seq: 2, kind: "agent_message", content: { text: "b" } }),
      item({ seq: 1, kind: "user_message", content: { text: "a" } }),
    ]);
    expect(messages.map((m) => m.content)).toEqual(["a", "b"]);
  });

  it("keeps a non-terminal tool call marked running with no output", () => {
    const [message] = normalizeItems([
      item({
        seq: 1,
        kind: "tool_call",
        status: "started",
        content: { tool: "execute_bash", call_id: "c1", input: "ls" },
      }),
    ]);
    expect(message.toolCall?.running).toBe(true);
    expect(message.toolCall?.output).toBeUndefined();
  });

  it("treats a run_end-finalized tool call as terminal, not running", () => {
    const [message] = normalizeItems([
      item({
        seq: 1,
        kind: "tool_call",
        status: "cancelled",
        content: {
          tool: "execute_bash",
          call_id: "c1",
          finalized_by: "run_end",
          end_reason: "cancelled",
        },
      }),
    ]);
    expect(message.toolCall?.running).toBe(false);
  });

  it("maps both PENDING approval flavours onto approval_request messages", () => {
    const messages = normalizeItems([
      item({
        seq: 1,
        kind: "approval",
        status: "started",
        content: { approval_kind: "tool", tool: "execute_bash", call_id: "c1" },
      }),
      item({
        seq: 2,
        kind: "approval",
        status: "started",
        content: {
          approval_kind: "mcp",
          tool: "search",
          request_id: "req-1",
          server_label: "docs",
        },
      }),
    ]);
    expect(messages[0].approval?.call_id).toBe("c1");
    expect(messages[1].approval?.call_id).toBe("req-1");
  });

  it("renders a RESOLVED approval as history, not a live prompt", () => {
    const [message] = normalizeItems([
      item({
        seq: 1,
        kind: "approval",
        status: "completed",
        content: {
          approval_kind: "tool",
          tool: "execute_bash",
          call_id: "c1",
          decision: "approve",
        },
      }),
    ]);
    expect(message.role).toBe("system");
    expect(message.approval).toBeUndefined();
    expect(message.content).toContain("approve");
  });

  it("renders compaction checkpoints and artifacts as system lines", () => {
    const messages = normalizeItems([
      item({ seq: 1, kind: "compaction", content: { summary: "earlier work" } }),
      item({ seq: 2, kind: "artifact", content: { title: "Report" } }),
    ]);
    expect(messages).toEqual([
      { role: "system", content: "earlier work" },
      { role: "system", content: "Report" },
    ]);
  });

  it("drops unrenderable kinds without dropping the rest", () => {
    const messages = normalizeItems([
      item({ seq: 1, kind: "reasoning", content: { summary: "thinking" } }),
      item({ seq: 2, kind: "agent_message", content: { text: "answer" } }),
    ]);
    expect(messages).toEqual([{ role: "assistant", content: "answer" }]);
  });
});

describe("mergeItems", () => {
  it("advances a cached item in place using its canonical id", () => {
    const started = item({
      seq: 1,
      kind: "tool_call",
      status: "started",
      revision: 0,
      content: { tool: "execute_bash", call_id: "c1" },
    });
    const completed = {
      ...started,
      status: "completed" as const,
      revision: 1,
      content: { ...started.content, output: "ok" },
    };
    const merged = mergeItems([started], [completed]);
    expect(merged).toHaveLength(1);
    expect(merged[0].status).toBe("completed");
  });

  it("refuses to regress an item to a stale revision", () => {
    const newer = item({ seq: 1, kind: "agent_message", revision: 3, content: { text: "new" } });
    const older = { ...newer, revision: 1, content: { text: "old" } };
    expect(mergeItems([newer], [older])[0].content.text).toBe("new");
  });
});

describe("item helpers", () => {
  it("classifies terminal statuses", () => {
    expect(isTerminal(item({ seq: 1, kind: "tool_call", status: "started" }))).toBe(false);
    for (const status of ["completed", "failed", "cancelled"] as const) {
      expect(isTerminal(item({ seq: 1, kind: "tool_call", status }))).toBe(true);
    }
  });

  it("reads the server tool taxonomy with an 'other' default", () => {
    expect(toolKind(item({ seq: 1, kind: "tool_call", content: { tool_kind: "plan" } }))).toBe("plan");
    expect(toolKind(item({ seq: 1, kind: "tool_call" }))).toBe("other");
  });
});
