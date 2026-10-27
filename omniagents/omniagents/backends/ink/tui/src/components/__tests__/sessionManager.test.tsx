import { ConnectionClosedError } from "@omniagents/client/lifecycle";
import React from "react";
import { EventEmitter } from "node:events";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { RPCClient } from "@omniagents/client/rpc/client";
import { cleanup, render } from "ink-testing-library";

const plain = (frame?: string) => stripAnsi(frame ?? "");

// ---------------------------------------------------------------------
// Harness — a real Ink render over a fake stdout/stdin, matching
// ./tasks.test.tsx (frame capture) and ./terminalCapture.test.tsx
// (paused-mode stdin). The manager's whole contract is "which surface
// did it read, and what does it let me press", so both halves are
// needed: assertions on a pure model cannot catch a key that never got
// wired to its RPC.
// ---------------------------------------------------------------------

const KEY = {
  down: "[B",
  up: "[A",
  enter: "\r",
  tab: "\t",
  escape: "",
} as const;

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Poll until `check` stops throwing. A fixed sleep raced the component's
 * initial load — the RPC stub resolves on a microtask but the effect
 * chain behind it does not, so a busy machine failed the assertion about
 * one run in three.
 */
async function waitFor(check: () => void, timeoutMs = 2000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      check();
      return;
    } catch (error) {
      if (Date.now() > deadline) throw error;
      await settle(10);
    }
  }
}

const ALL_GRANTS = [
  "search_threads",
  "update_thread",
  "fork_session",
  "set_session_hold",
];

/** A canonical thread row as list_threads returns it. */
function thread(id: string, overrides: Record<string, unknown> = {}) {
  return {
    thread_id: id,
    user_id: "user-1",
    status: "active",
    title: `Thread ${id}`,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_000,
    last_seq: 2,
    turn_count: 1,
    item_count: 2,
    parent_thread_id: null,
    branched_from_item_id: null,
    usage: {},
    compaction: {},
    pinned: false,
    metadata: {},
    source: "recorded",
    projected_at: null,
    schema_version: 1,
    ...overrides,
  };
}

/**
 * The operations the screen performs, as mocks with readable signatures;
 * `asClient` routes the shared client's wire requests to them and wraps
 * their answers in the wire shapes the organization client validates.
 */
function makeClient(grants: string[] = ALL_GRANTS) {
  return {
    events: new EventEmitter(),
    granted: new Set(grants),
    initializeResult: undefined as unknown,
    // Newest first, as list_threads pages them.
    listThreads: vi.fn(async () => ({
      threads: [
        thread("a", { updated_at: 1_700_000_002 }),
        thread("b", { updated_at: 1_700_000_001 }),
      ],
    })),
    searchThreads: vi.fn(async (_query: string, _options: unknown) => ({
      results: [{ thread_id: "b", preview: "match", thread: thread("b") }],
    })),
    updateThread: vi.fn(async (_id: string, _changes: unknown) => ({})),
    forkSession: vi.fn(
      async (_id: string): Promise<{ new_session_id: string }> => ({
        new_session_id: "forked-1",
      }),
    ),
    setSessionHold: vi.fn(async (_id: string, _hold: boolean) => ({})),
    deleteSession: vi.fn(async (_id: string) => true),
  };
}

/**
 * Derived rather than hand-written: a hand-written shape widens every
 * mock to `Mock<any[], unknown>`, which then rejects the specific
 * resolved values below.
 */
type ClientStub = ReturnType<typeof makeClient>;

type Params = Record<string, unknown>;

async function route(stub: ClientStub, method: string, params: Params) {
  switch (method) {
    case "list_threads": {
      const page = await stub.listThreads();
      return {
        ...page,
        next_cursor: null,
        has_more: false,
        total: page.threads.length,
      };
    }
    case "search_threads": {
      const { query, ...options } = params;
      const page = await stub.searchThreads(query as string, options);
      return {
        query,
        results: page.results.map((hit) => ({
          match_count: 1,
          match_kind: "message",
          item_id: null,
          turn_id: null,
          seq: null,
          rank_detail: 1,
          ...hit,
        })),
        next_cursor: null,
        has_more: false,
        total: page.results.length,
      };
    }
    case "update_thread": {
      const { thread_id, ...changes } = params;
      await stub.updateThread(thread_id as string, changes);
      return {
        thread: thread(thread_id as string, changes),
        changed: Object.keys(changes),
        cascaded_thread_ids: [],
        inaccessible_descendant_count: 0,
      };
    }
    case "fork_session": {
      const forked = await stub.forkSession(params.session_id as string);
      return {
        session_id: params.session_id,
        branched_from_item_id: null,
        ...forked,
      };
    }
    case "set_session_hold":
      return stub.setSessionHold(
        params.session_id as string,
        params.hold as boolean,
      );
    case "delete_session":
      return stub.deleteSession(params.session_id as string);
    default:
      throw new Error(`unexpected ${method}`);
  }
}

/** Adapt the stub to the surface SessionManager actually touches. */
function asClient(stub: ClientStub): RPCClient {
  return {
    get initializeResult() {
      return stub.initializeResult;
    },
    supportsExperimentalOperation: (op: string) => stub.granted.has(op),
    request: (method: string, params: Params) => route(stub, method, params),
    on: (event: string, handler: (...args: unknown[]) => void) => {
      stub.events.on(event, handler);
      return () => stub.events.off(event, handler);
    },
    onConnectionState: (handler: (state: string) => void) => {
      const connected = () => handler("connected");
      stub.events.on("connected", connected);
      return () => stub.events.off("connected", connected);
    },
  } as unknown as RPCClient;
}

let SessionManager: (typeof import("../SessionManager.js"))["SessionManager"];

beforeAll(async () => {
  ({ SessionManager } = await import("../SessionManager.js"));
});

afterEach(() => {
  cleanup();
});

async function mount(
  stub: ClientStub,
  handlers: {
    onOpen?: (id: string) => void;
    onNew?: () => void;
    onEmpty?: () => void;
    onExit?: () => void;
  } = {},
) {
  const instance = render(
    <KeypressProvider>
      <SessionManager
        client={asClient(stub)}
        onOpen={handlers.onOpen ?? (() => undefined)}
        onNew={handlers.onNew ?? (() => undefined)}
        onEmpty={handlers.onEmpty}
        onExit={handlers.onExit}
      />
    </KeypressProvider>,
  );
  await settle();
  return { ...instance, instance };
}

// ---------------------------------------------------------------------

describe("SessionManager surface selection", () => {
  it("reads the organized thread list", async () => {
    const stub = makeClient();
    const { lastFrame } = await mount(stub);
    expect(stub.listThreads).toHaveBeenCalled();
    expect(plain(lastFrame())).toContain("Thread a");
  });

  it("reports an empty first load so the caller can skip the screen", async () => {
    const stub = makeClient();
    stub.listThreads.mockResolvedValueOnce({ threads: [] });
    const onEmpty = vi.fn();
    await mount(stub, { onEmpty });
    await waitFor(() => expect(onEmpty).toHaveBeenCalledTimes(1));
  });

  it("does not report empty when there are conversations", async () => {
    const onEmpty = vi.fn();
    await mount(makeClient(), { onEmpty });
    expect(onEmpty).not.toHaveBeenCalled();
  });

  it("keeps the screen usable when the listing fails", async () => {
    const stub = makeClient();
    stub.listThreads.mockRejectedValueOnce(new Error("boom"));
    const onEmpty = vi.fn();
    const { lastFrame } = await mount(stub, { onEmpty });
    expect(plain(lastFrame())).toContain("boom");
    // An error is not an absence — routing away would hide it.
    expect(onEmpty).not.toHaveBeenCalled();
  });
});

describe("SessionManager keys", () => {
  it("opens the selected conversation on enter", async () => {
    const onOpen = vi.fn();
    const { stdin } = await mount(makeClient(), { onOpen });
    stdin.write(KEY.enter);
    await settle();
    expect(onOpen).toHaveBeenCalledWith("a");
  });

  it("moves the cursor before opening", async () => {
    const onOpen = vi.fn();
    const { stdin } = await mount(makeClient(), { onOpen });
    stdin.write(KEY.down);
    await settle();
    stdin.write(KEY.enter);
    await settle();
    expect(onOpen).toHaveBeenCalledWith("b");
  });

  it("starts a new conversation on tab", async () => {
    const onNew = vi.fn();
    const { stdin } = await mount(makeClient(), { onNew });
    stdin.write(KEY.tab);
    await settle();
    expect(onNew).toHaveBeenCalled();
  });

  it("goes back to the conversation on esc when opened from chat", async () => {
    const onExit = vi.fn();
    const onNew = vi.fn();
    const { stdin, lastFrame } = await mount(makeClient(), { onExit, onNew });
    await waitFor(() => expect(plain(lastFrame())).toContain("esc back"));
    stdin.write(KEY.escape);
    // A lone Esc waits out the parser's disambiguation window.
    await waitFor(() => expect(onExit).toHaveBeenCalledTimes(1));
    expect(onNew).not.toHaveBeenCalled();
  });

  it("has no esc hint at launch, where there is nothing to go back to", async () => {
    const { lastFrame } = await mount(makeClient());
    await waitFor(() => expect(plain(lastFrame())).toContain("enter open"));
    expect(plain(lastFrame())).not.toContain("esc back");
  });

  it("pins through update_thread and refetches", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.write("p");
    await settle();
    expect(stub.updateThread).toHaveBeenCalledWith("a", { pinned: true });
    expect(stub.listThreads).toHaveBeenCalledTimes(2);
  });

  it("archives through update_thread", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.write("a");
    await settle();
    expect(stub.updateThread).toHaveBeenCalledWith("a", { status: "archived" });
  });

  it("forks and opens the child rather than returning to the list", async () => {
    const stub = makeClient();
    const onOpen = vi.fn();
    const { stdin } = await mount(stub, { onOpen });
    stdin.write("f");
    await settle();
    expect(stub.forkSession).toHaveBeenCalledWith("a");
    expect(onOpen).toHaveBeenCalledWith("forked-1");
  });

  it("places a retention hold", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.write("h");
    await settle();
    expect(stub.setSessionHold).toHaveBeenCalledWith("a", true);
  });

  it("requires a confirmation before deleting", async () => {
    const stub = makeClient();
    const { stdin, lastFrame } = await mount(stub);
    stdin.write("d");
    await settle();
    expect(stub.deleteSession).not.toHaveBeenCalled();
    expect(plain(lastFrame())).toContain("cannot be undone");
    stdin.write("y");
    await settle();
    expect(stub.deleteSession).toHaveBeenCalledWith("a");
  });

  it("abandons a delete on n", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.write("d");
    await settle();
    stdin.write("n");
    await settle();
    expect(stub.deleteSession).not.toHaveBeenCalled();
  });

  it("declines organize keys on the degraded surface instead of calling", async () => {
    const stub = makeClient([]);
    const { stdin, lastFrame } = await mount(stub);
    stdin.write("p");
    await settle();
    expect(stub.updateThread).not.toHaveBeenCalled();
    expect(plain(lastFrame())).toContain("does not support organizing");
  });
});

describe("SessionManager search", () => {
  it("delegates to search_threads when granted", async () => {
    const stub = makeClient();
    const { stdin, lastFrame } = await mount(stub);
    stdin.write("/");
    await settle();
    for (const ch of "abc") stdin.write(ch);
    await settle();
    stdin.write(KEY.enter);
    await settle();
    expect(stub.searchThreads).toHaveBeenCalledWith("abc", { limit: 100 });
    expect(plain(lastFrame())).toContain("match");
  });

  it("filters locally when search is not granted", async () => {
    const stub = makeClient([]);
    const { stdin } = await mount(stub);
    stdin.write("/");
    await settle();
    stdin.write("z");
    await settle();
    stdin.write(KEY.enter);
    await settle();
    expect(stub.searchThreads).not.toHaveBeenCalled();
    // Re-read the list, then narrow it in memory.
    expect(stub.listThreads).toHaveBeenCalledTimes(2);
  });

  it("abandons the query on escape and hands the keyboard back", async () => {
    const stub = makeClient();
    const onOpen = vi.fn();
    const { stdin } = await mount(stub, { onOpen });
    stdin.write("/");
    await settle();
    stdin.write("q");
    await settle();
    stdin.write(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(stub.searchThreads).not.toHaveBeenCalled();
    // Proves escape actually left search mode rather than doing nothing:
    // Enter now opens the selection instead of submitting a query.
    stdin.write(KEY.enter);
    await settle();
    expect(onOpen).toHaveBeenCalledWith("a");
    expect(stub.searchThreads).not.toHaveBeenCalled();
  });
});

describe("SessionManager races", () => {
  it("uses the new selection for an arrow-and-Enter burst", async () => {
    const stub = makeClient();
    const onOpen = vi.fn();
    const { stdin } = await mount(stub, { onOpen });
    stdin.write(KEY.down + KEY.enter);
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith("b"));
  });
  it("does not treat pasted commands as actions", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    for (const command of ["p", "a", "f", "d"])
      stdin.write(`\x1b[200~${command}\x1b[201~`);
    await settle();
    expect(stub.updateThread).not.toHaveBeenCalled();
    expect(stub.forkSession).not.toHaveBeenCalled();
    expect(stub.deleteSession).not.toHaveBeenCalled();
  });
  it("reads a whole rename-and-submit burst without dropping characters", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.write("r!" + KEY.enter);
    await waitFor(() =>
      expect(stub.updateThread).toHaveBeenCalledWith("a", {
        title: "Thread a!",
      }),
    );
  });
  it("does not duplicate forks or open a late result after leaving", async () => {
    const stub = makeClient();
    let resolve!: (value: { new_session_id: string }) => void;
    stub.forkSession.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const onOpen = vi.fn(),
      onNew = vi.fn();
    const { stdin } = await mount(stub, { onOpen, onNew });
    stdin.write("ff" + KEY.tab);
    await settle();
    expect(stub.forkSession).toHaveBeenCalledTimes(1);
    resolve({ new_session_id: "late" });
    await settle();
    expect(onOpen).not.toHaveBeenCalled();
    expect(onNew).toHaveBeenCalledTimes(1);
  });
});

it("keeps the rename target when a refresh removes it from the list", async () => {
  const stub = makeClient();
  const { stdin } = await mount(stub);
  stdin.write("r");
  await settle();
  stub.listThreads.mockResolvedValue({ threads: [thread("b")] });
  stub.events.emit("thread_updated", {
    thread_id: "b",
    changed: ["title"],
    thread: thread("b", { updated_at: 1_700_000_009 }),
    cascaded_thread_ids: [],
  });
  await settle();
  stdin.write("!" + KEY.enter);
  await waitFor(() =>
    expect(stub.updateThread).toHaveBeenCalledWith("a", { title: "Thread a!" }),
  );
});

it("ignores an earlier list response after a search finishes", async () => {
  const stub = makeClient();
  let resolve!: (value: { threads: ReturnType<typeof thread>[] }) => void;
  stub.listThreads.mockReturnValueOnce(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const { stdin, lastFrame } = await mount(stub);
  stdin.write("/match" + KEY.enter);
  await settle();
  resolve({ threads: [thread("a")] });
  await settle();
  expect(plain(lastFrame())).toContain("Thread b");
  expect(plain(lastFrame())).not.toContain("Thread a");
});

it("does not reuse an old delete confirmation during a cancel/select/delete/yes burst", async () => {
  const stub = makeClient();
  const { stdin } = await mount(stub);
  stdin.write("d");
  await settle();
  stdin.write("n" + KEY.down + "dy");
  await settle();
  expect(stub.deleteSession).not.toHaveBeenCalled();
  stdin.write("y");
  await waitFor(() => expect(stub.deleteSession).toHaveBeenCalledWith("b"));
});

it("re-reads the list after a lost fork reply instead of replaying or warning", async () => {
  const stub = makeClient();
  stub.forkSession.mockRejectedValue(new ConnectionClosedError("lost reply"));
  const { stdin, lastFrame } = await mount(stub);
  const listsBefore = stub.listThreads.mock.calls.length;
  stdin.write("f");
  await settle();
  expect(plain(lastFrame())).not.toContain("forking");
  stub.events.emit("connected");
  await settle();
  expect(stub.forkSession).toHaveBeenCalledTimes(1);
  expect(stub.listThreads.mock.calls.length).toBeGreaterThan(listsBefore);
  expect(plain(lastFrame())).not.toContain("outcome unknown");
  expect(plain(lastFrame())).not.toContain("fork failed");
});

it("refetches immediately after a lost mutation reply when already reconnected", async () => {
  const stub = makeClient();
  stub.initializeResult = { agent_host: { default_environment_id: null } };
  stub.updateThread.mockRejectedValueOnce(new ConnectionClosedError("lost"));
  const { stdin, lastFrame } = await mount(stub);
  const listsBefore = stub.listThreads.mock.calls.length;
  stdin.write("a");
  await waitFor(() =>
    expect(stub.listThreads.mock.calls.length).toBeGreaterThan(listsBefore),
  );
  await settle();
  expect(plain(lastFrame())).not.toContain("outcome unknown");
  expect(plain(lastFrame())).not.toContain("failed");
});
