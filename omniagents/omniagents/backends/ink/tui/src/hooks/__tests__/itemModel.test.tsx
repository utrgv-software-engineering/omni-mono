/**
 * Canonical item-model flow through useMessagesOptimized: cold hydration
 * from list_items (with the legacy get_session_history fallback), live
 * item_updated reconciliation (transcript / plan / run_diff), the
 * get_run_diff fetch at run_end, and client_request_resolved dismissing a
 * stale escalation banner.
 */

import React from "react";
import { EventEmitter } from "node:events";
import { describe, expect, it, beforeAll, afterEach, vi } from "vitest";

import type { RPCClient } from "../../rpc/client.js";
import { useAppStore } from "../../store/appStore.js";
import { planStepsToTasks } from "../../components/task-model.js";
import type { ConversationItem } from "../../types/conversation.js";

type InkModule = typeof import("ink");
type InkRender = InkModule["render"];

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function item(overrides: Partial<ConversationItem>): ConversationItem {
  return {
    item_id: "item-0",
    thread_id: "sess",
    turn_id: "turn-1",
    seq: 0,
    kind: "agent_message",
    status: "completed",
    role: null,
    created_at: 0,
    updated_at: 0,
    completed_at: null,
    revision: 1,
    content: {},
    source_ref: {},
    long_lived: false,
    source: "recorded",
    schema_version: 1,
    ...overrides,
  };
}

class FakeClient extends EventEmitter {
  granted = new Set(["get_plan", "get_run_diff", "item_updated"]);
  items: ConversationItem[] = [];
  failListItems = false;
  legacyHistory: Array<Record<string, unknown>> = [];
  planSteps: unknown[] | null = null;
  runDiffResult: Record<string, unknown> | null = null;

  private functions = new Map<
    string,
    (args: Record<string, unknown>) => Promise<unknown>
  >();

  registerClientFunction(
    name: string,
    handler: (args: Record<string, unknown>) => Promise<unknown>,
  ) {
    this.functions.set(name, handler);
  }

  unregisterClientFunction(name: string) {
    this.functions.delete(name);
  }

  callClientFunction(name: string, args: Record<string, unknown>) {
    const handler = this.functions.get(name);
    if (!handler) throw new Error(`missing handler: ${name}`);
    return handler(args);
  }

  hasClientFunction(name: string): boolean {
    return this.functions.has(name);
  }

  isGranted(operation: string): boolean {
    return this.granted.has(operation);
  }

  async listAllItems(_threadId: string): Promise<ConversationItem[]> {
    if (this.failListItems) throw new Error("thread not found");
    return this.items;
  }

  async getSessionHistory(_sessionId: string) {
    return this.legacyHistory;
  }

  async getPlan(_threadId: string) {
    return {
      thread_id: "sess",
      scope: "main",
      plan: this.planSteps ? { steps: this.planSteps } : null,
      plans: [],
    };
  }

  async getRunDiff(_threadId: string) {
    return { thread_id: "sess", turn_id: null, run_diff: this.runDiffResult };
  }

  async listQueue(_sessionId: string) {
    return { session_id: "sess", depth: 0, items: [] };
  }
}

let inkRender: InkRender;
let Text: InkModule["Text"];
let useMessagesOptimized: (typeof import("../useMessagesNew.js"))["useMessagesOptimized"];

const instances: Array<ReturnType<InkRender>> = [];

beforeAll(async () => {
  const inkModule = await import("ink");
  inkRender = inkModule.render;
  Text = inkModule.Text;
  ({ useMessagesOptimized } = await import("../useMessagesNew.js"));
});

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
  useAppStore.getState().actions.clearMessages();
  useAppStore.getState().actions.setEscalation(null);
  useAppStore.getState().actions.setSessionId(undefined);
});

function mount(client: FakeClient, sessionId?: string) {
  const Harness = () => {
    useMessagesOptimized({
      client: client as unknown as RPCClient,
      sessionId,
      rpcReady: true,
    });
    return <Text>ok</Text>;
  };
  const instance = inkRender(<Harness />, {
    stdout: process.stdout,
    stderr: process.stderr,
    stdin: process.stdin,
    debug: false,
    exitOnCtrlC: false,
    patchConsole: false,
  });
  instances.push(instance);
}

describe("item-model hydration", () => {
  it("hydrates the transcript and task dock from the persisted model", async () => {
    const client = new FakeClient();
    client.items = [
      item({
        item_id: "i1",
        seq: 1,
        kind: "user_message",
        content: { text: "hello" },
      }),
      item({
        item_id: "i2",
        seq: 2,
        kind: "tool_call",
        content: {
          tool: "read_file",
          input: "{}",
          call_id: "c1",
          output: "ok",
        },
      }),
      item({
        item_id: "i3",
        seq: 3,
        kind: "agent_message",
        content: { text: "done" },
      }),
    ];
    client.planSteps = [
      {
        id: "1",
        subject: "Investigate",
        active_form: "Investigating",
        status: "completed",
        owner: "agent",
        blocks: [],
        blocked_by: [],
      },
      {
        id: "2",
        subject: "Fix",
        active_form: "Fixing",
        status: "blocked",
        owner: "agent",
        blocks: [],
        blocked_by: ["1"],
      },
    ];

    mount(client, "sess");
    await flush();

    const state = useAppStore.getState();
    expect(state.conversationItems.map((i) => i.item_id)).toEqual([
      "i1",
      "i2",
      "i3",
    ]);
    expect(
      state.messages.map((m) => ({ role: m.role, content: m.content })),
    ).toEqual([
      { role: "user", content: "hello" },
      { role: "tool_call", content: "" },
      { role: "assistant", content: "done" },
    ]);
    expect(state.messages[1]!.toolCall).toMatchObject({
      tool: "read_file",
      call_id: "c1",
      output: "ok",
      running: false,
    });
    expect(state.tasks).toEqual([
      {
        id: "1",
        subject: "Investigate",
        activeForm: "Investigating",
        status: "completed",
        owner: "agent",
        blockedBy: [],
      },
      {
        id: "2",
        subject: "Fix",
        activeForm: "Fixing",
        status: "blocked",
        owner: "agent",
        blockedBy: ["1"],
      },
    ]);
  });

  it("falls back to legacy history when the item read fails", async () => {
    const client = new FakeClient();
    client.failListItems = true;
    client.legacyHistory = [
      { role: "user", content: "hi", timestamp: "2026-08-05T00:00:00Z" },
      { role: "assistant", content: "hey", timestamp: "2026-08-05T00:00:01Z" },
    ];

    mount(client, "sess");
    await flush();
    await flush();

    expect(
      useAppStore.getState().messages.map((m) => [m.role, m.content]),
    ).toEqual([
      ["user", "hi"],
      ["assistant", "hey"],
    ]);
  });
});

describe("item_updated reconciliation", () => {
  it("re-derives the idle transcript, updates the plan dock and the diff panel", async () => {
    const client = new FakeClient();
    client.items = [
      item({
        item_id: "i1",
        seq: 1,
        kind: "user_message",
        content: { text: "hello" },
      }),
    ];
    mount(client, "sess");
    useAppStore.getState().actions.setSessionId("sess");
    await flush();

    // Transcript item while idle → merged and re-rendered.
    client.emit("item_updated", {
      session_id: "sess",
      thread_id: "sess",
      item_id: "i2",
      kind: "agent_message",
      status: "completed",
      revision: 1,
      item_seq: 2,
      content: { text: "from another client" },
    });
    // Plan item → task dock, not transcript.
    client.emit("item_updated", {
      session_id: "sess",
      thread_id: "sess",
      item_id: "i3",
      kind: "plan",
      status: "started",
      revision: 1,
      item_seq: 3,
      content: {
        steps: [
          {
            id: "1",
            subject: "Ship it",
            active_form: "Shipping",
            status: "in_progress",
            owner: "agent",
            blocks: [],
            blocked_by: [],
          },
        ],
      },
    });
    // run_diff item → changes panel, not transcript.
    client.emit("item_updated", {
      session_id: "sess",
      thread_id: "sess",
      item_id: "i4",
      kind: "run_diff",
      status: "completed",
      revision: 1,
      item_seq: 4,
      content: {
        item_id: "i4",
        run_id: "run-1",
        diff: "--- a/x\n+++ b/x\n",
        files: [
          {
            path: "x",
            change_type: "modified",
            additions: 2,
            deletions: 1,
            opaque: false,
            baseline_unknown: false,
          },
        ],
        stats: { files_changed: 1, additions: 2, deletions: 1 },
        truncated: false,
        files_truncated: false,
        status: "completed",
        revision: 1,
        updated_at: 1,
      },
    });
    // A different session's item must be ignored.
    client.emit("item_updated", {
      session_id: "other",
      thread_id: "other",
      item_id: "ix",
      kind: "agent_message",
      status: "completed",
      revision: 1,
      item_seq: 9,
      content: { text: "wrong session" },
    });
    await flush();
    await flush();

    const state = useAppStore.getState();
    expect(state.messages.map((m) => [m.role, m.content])).toEqual([
      ["user", "hello"],
      ["assistant", "from another client"],
    ]);
    expect(state.tasks).toEqual([
      {
        id: "1",
        subject: "Ship it",
        activeForm: "Shipping",
        status: "in_progress",
        owner: "agent",
        blockedBy: [],
      },
    ]);
    expect(state.runDiff).toMatchObject({
      run_id: "run-1",
      stats: { files_changed: 1, additions: 2, deletions: 1 },
    });
  });

  it("leaves the transcript alone while a local run is streaming", async () => {
    const client = new FakeClient();
    mount(client, "sess");
    useAppStore.getState().actions.setSessionId("sess");
    await flush();
    useAppStore.getState().actions.setCurrentRunId("run-live");

    client.emit("item_updated", {
      session_id: "sess",
      thread_id: "sess",
      item_id: "i9",
      kind: "agent_message",
      status: "completed",
      revision: 1,
      item_seq: 9,
      content: { text: "durable echo of the live stream" },
    });
    await flush();
    await flush();

    const state = useAppStore.getState();
    // Cached for the next re-derivation, but not painted mid-run.
    expect(state.conversationItems.map((i) => i.item_id)).toContain("i9");
    expect(state.messages).toEqual([]);
    useAppStore.getState().actions.setCurrentRunId(null);
  });
});

describe("run_end diff fetch", () => {
  it("fetches the aggregated diff when the run ends", async () => {
    const client = new FakeClient();
    client.runDiffResult = {
      item_id: "d1",
      run_id: "run-1",
      diff: "",
      files: [],
      stats: { files_changed: 2, additions: 5, deletions: 3 },
      truncated: false,
      files_truncated: false,
      status: "completed",
      revision: 1,
      updated_at: 1,
    };
    mount(client, "sess");
    await flush();

    client.emit("run_end", {
      run_id: "run-1",
      session_id: "sess",
      end_reason: "completed",
    });
    await flush();
    await flush();

    expect(useAppStore.getState().runDiff).toMatchObject({
      run_id: "run-1",
      stats: { files_changed: 2, additions: 5, deletions: 3 },
    });
  });
});

describe("live commentary", () => {
  it("keeps the model's commentary between tool calls in the transcript", async () => {
    const client = new FakeClient();
    mount(client, "sess");
    await flush();
    client.emit("run_started", { session_id: "sess", run_id: "run" });
    client.emit("message_output", {
      session_id: "sess",
      content: "Reading the config first.",
    });
    client.emit("tool_called", {
      session_id: "sess",
      tool: "read_file",
      input: "{}",
      call_id: "c1",
    });
    client.emit("message_output", { session_id: "sess", content: "" });
    client.emit("message_output", {
      session_id: "sess",
      content: "The config is fine.",
    });
    client.emit("run_end", {
      session_id: "sess",
      run_id: "run",
      end_reason: "completed",
    });
    await flush();
    expect(
      useAppStore.getState().messages.map((m) => [m.role, m.content]),
    ).toEqual([
      ["assistant", "Reading the config first."],
      ["tool_call", ""],
      ["assistant", "The config is fine."],
    ]);
  });
});

describe("client_request_resolved", () => {
  it("dismisses an escalation banner another client answered", async () => {
    const client = new FakeClient();
    mount(client, "sess");
    await flush();

    // Blocking escalate: banner shows, response promise stays parked.
    void client.callClientFunction("escalate", {
      message: "need a decision",
      request_id: "req-1",
      session_id: "sess",
    });
    await flush();
    expect(useAppStore.getState().escalation?.request_id).toBe("req-1");

    client.emit("client_request_resolved", {
      request_id: "req-1",
      session_id: "sess",
      reason: "resolved_by_other_client",
    });
    await flush();

    expect(useAppStore.getState().escalation).toBeNull();
  });
});

describe("session isolation", () => {
  it("ignores messages, run starts and prompts for another session", async () => {
    const client = new FakeClient();
    mount(client, "selected");
    await flush();
    client.emit("run_started", {
      session_id: "other",
      run_id: "foreign",
      prompt: "wrong",
    });
    client.emit("message_output", { session_id: "other", content: "wrong" });
    client.emit("tool_called", {
      session_id: "other",
      tool: "bash",
      call_id: "foreign",
    });
    client.emit("tool_approval_requested", {
      session_id: "other",
      call_id: "foreign",
      tool_name: "bash",
    });
    client.emit("run_end", {
      session_id: "other",
      run_id: "foreign",
      end_reason: "completed",
    });
    await flush();
    expect(useAppStore.getState().messages).toEqual([]);
    expect(useAppStore.getState().currentRunId).toBeNull();
    expect(useAppStore.getState().approval).toBeNull();
  });

  it("does not overwrite live messages with an older hydration response", async () => {
    const client = new FakeClient();
    let complete!: (items: ConversationItem[]) => void;
    client.listAllItems = () =>
      new Promise((resolve) => {
        complete = resolve;
      });
    mount(client, "sess");
    await flush();
    client.emit("run_started", { session_id: "sess", run_id: "run" });
    client.emit("message_output", {
      session_id: "sess",
      content: "new message",
    });
    client.emit("run_end", {
      session_id: "sess",
      run_id: "run",
      end_reason: "completed",
    });
    await flush();
    complete([]);
    await flush();
    expect(
      useAppStore.getState().messages.some((m) => m.content === "new message"),
    ).toBe(true);
  });

  it("invalidates reads when a session is switched without unmounting", async () => {
    const client = new FakeClient();
    let finishOld!: (items: ConversationItem[]) => void;
    client.listAllItems = (id) =>
      id === "old"
        ? new Promise((resolve) => {
            finishOld = resolve;
          })
        : Promise.resolve([
            item({ item_id: "new", content: { text: "new session" } }),
          ]);
    const Harness = ({ id }: { id: string }) => {
      useMessagesOptimized({
        client: client as unknown as RPCClient,
        sessionId: id,
        rpcReady: true,
      });
      return <Text>ok</Text>;
    };
    const instance = inkRender(<Harness id="old" />, {
      exitOnCtrlC: false,
      patchConsole: false,
    });
    instances.push(instance);
    await flush();
    instance.rerender(<Harness id="new" />);
    await flush();
    finishOld([item({ item_id: "old", content: { text: "old session" } })]);
    await flush();
    expect(
      useAppStore.getState().conversationItems.map((i) => i.item_id),
    ).toEqual(["new"]);
  });

  it("hydrates again when replay requests a resync", async () => {
    const client = new FakeClient();
    const sync = vi.fn(async (_id: string, apply: (value: any) => void) =>
      apply({
        active_run_id: null,
        run_active: false,
        snapshot: { items: [], queue: [] },
      }),
    );
    (client as any).synchronizeSession = sync;
    mount(client, "sess");
    await flush();
    expect(sync).toHaveBeenCalledTimes(1);
    (client as any).onResyncRequired("sess");
    await flush();
    expect(sync).toHaveBeenCalledTimes(2);
    expect(useAppStore.getState().thinking).toBe(false);
  });
});

describe("planStepsToTasks", () => {
  it("rejects malformed payloads instead of blanking the dock", () => {
    expect(planStepsToTasks(undefined)).toBeNull();
    expect(planStepsToTasks("nope")).toBeNull();
    expect(planStepsToTasks([{ id: 1, subject: "bad id" }])).toBeNull();
  });

  it("projects wire snake_case onto the dock shape", () => {
    expect(
      planStepsToTasks([
        {
          id: "7",
          subject: "Do it",
          active_form: "Doing it",
          status: "pending",
          owner: "agent",
          blocks: ["8"],
          blocked_by: [],
        },
      ]),
    ).toEqual([
      {
        id: "7",
        subject: "Do it",
        activeForm: "Doing it",
        status: "pending",
        owner: "agent",
        blockedBy: [],
      },
    ]);
  });
});
