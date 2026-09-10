import { ConnectionClosedError } from "../../rpc/lifecycle.js";
import React from "react";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { RPCClient } from "../../rpc/client.js";

// ---------------------------------------------------------------------
// Harness — a real Ink render over a fake stdout/stdin, matching
// ./tasks.test.tsx (frame capture) and ./terminalCapture.test.tsx
// (paused-mode stdin). The manager's whole contract is "which surface
// did it read, and what does it let me press", so both halves are
// needed: assertions on a pure model cannot catch a key that never got
// wired to its RPC.
// ---------------------------------------------------------------------

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 120;
  rows = 40;

  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") this.frames.push(chunk);
    });
  }

  /** Last frame carrying content; Ink's trailing cursor escapes are not one. */
  lastFrame(): string {
    for (let index = this.frames.length - 1; index >= 0; index -= 1) {
      const frame = stripAnsi(this.frames[index] ?? "");
      if (frame.trim()) return frame;
    }
    return "";
  }
}

class FakeStdin extends EventEmitter {
  isTTY = true;
  private queue: string[] = [];
  setRawMode() {}
  setEncoding() {}
  resume() {}
  pause() {}
  read() {
    return this.queue.shift() ?? null;
  }
  unref() {}
  ref() {}
  /** Ink 7 reads stdin paused — "readable" + read(), never "data". */
  press(sequence: string) {
    this.queue.push(sequence);
    this.emit("readable");
  }
}

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
  "list_threads",
  "search_threads",
  "update_thread",
  "fork_session",
  "set_session_hold",
];

function thread(id: string, overrides: Record<string, unknown> = {}) {
  return {
    thread_id: id,
    title: `Thread ${id}`,
    status: "active",
    created_at: 1_700_000_000,
    updated_at: 1_700_000_000,
    item_count: 2,
    pinned: false,
    ...overrides,
  };
}

function makeClient(grants: string[] = ALL_GRANTS) {
  return {
    events: new EventEmitter(),
    granted: new Set(grants),
    listThreads: vi.fn(async () => ({ threads: [thread("a"), thread("b")] })),
    searchThreads: vi.fn(async () => ({
      results: [{ thread_id: "b", preview: "match", thread: thread("b") }],
    })),
    listSessions: vi.fn(async () => [
      { id: "legacy-1", created_at: "2026-01-01T00:00:00Z", message_count: 4 },
    ]),
    updateThread: vi.fn(async () => ({})),
    forkSession: vi.fn(async () => ({ new_session_id: "forked-1" })),
    setSessionHold: vi.fn(async () => ({})),
    deleteSession: vi.fn(async () => true),
  };
}

/**
 * Derived rather than hand-written: a hand-written shape widens every
 * mock to `Mock<any[], unknown>`, which then rejects the specific
 * resolved values below.
 */
type ClientStub = ReturnType<typeof makeClient>;

/** Adapt the stub to the surface SessionManager actually touches. */
function asClient(stub: ClientStub): RPCClient {
  return {
    ...stub,
    defaultEnvironmentId: () => null,
    isGranted: (op: string) => stub.granted.has(op),
    on: stub.events.on.bind(stub.events),
    off: stub.events.off.bind(stub.events),
  } as unknown as RPCClient;
}

type Render = (typeof import("ink"))["render"];
const instances: Array<ReturnType<Render>> = [];
let inkRender: Render;
let SessionManager: (typeof import("../SessionManager.js"))["SessionManager"];

beforeAll(async () => {
  inkRender = (await import("ink")).render;
  ({ SessionManager } = await import("../SessionManager.js"));
});

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
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
  const stdout = new TestStdout();
  const stdin = new FakeStdin();
  const instance = inkRender(
    <KeypressProvider>
      <SessionManager
        client={asClient(stub)}
        onOpen={handlers.onOpen ?? (() => undefined)}
        onNew={handlers.onNew ?? (() => undefined)}
        onEmpty={handlers.onEmpty}
        onExit={handlers.onExit}
      />
    </KeypressProvider>,
    {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  instances.push(instance);
  await settle();
  return { stdout, stdin, instance };
}

// ---------------------------------------------------------------------

describe("SessionManager surface selection", () => {
  it("reads the organized surface when list_threads is granted", async () => {
    const stub = makeClient();
    const { stdout } = await mount(stub);
    expect(stub.listThreads).toHaveBeenCalled();
    expect(stub.listSessions).not.toHaveBeenCalled();
    expect(stdout.lastFrame()).toContain("Thread a");
  });

  it("falls back to list_sessions when the grant is absent", async () => {
    const stub = makeClient([]);
    const { stdout } = await mount(stub);
    expect(stub.listSessions).toHaveBeenCalled();
    expect(stub.listThreads).not.toHaveBeenCalled();
    // The degraded path must SAY it is degraded rather than silently
    // offering keys that cannot work.
    expect(stdout.lastFrame()).toContain("predates conversation organizing");
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
    const { stdout } = await mount(stub, { onEmpty });
    expect(stdout.lastFrame()).toContain("boom");
    // An error is not an absence — routing away would hide it.
    expect(onEmpty).not.toHaveBeenCalled();
  });
});

describe("SessionManager keys", () => {
  it("opens the selected conversation on enter", async () => {
    const onOpen = vi.fn();
    const { stdin } = await mount(makeClient(), { onOpen });
    stdin.press(KEY.enter);
    await settle();
    expect(onOpen).toHaveBeenCalledWith("a");
  });

  it("moves the cursor before opening", async () => {
    const onOpen = vi.fn();
    const { stdin } = await mount(makeClient(), { onOpen });
    stdin.press(KEY.down);
    await settle();
    stdin.press(KEY.enter);
    await settle();
    expect(onOpen).toHaveBeenCalledWith("b");
  });

  it("starts a new conversation on tab", async () => {
    const onNew = vi.fn();
    const { stdin } = await mount(makeClient(), { onNew });
    stdin.press(KEY.tab);
    await settle();
    expect(onNew).toHaveBeenCalled();
  });

  it("goes back to the conversation on esc when opened from chat", async () => {
    const onExit = vi.fn();
    const onNew = vi.fn();
    const { stdin, stdout } = await mount(makeClient(), { onExit, onNew });
    await waitFor(() => expect(stdout.lastFrame()).toContain("esc back"));
    stdin.press(KEY.escape);
    // A lone Esc waits out the parser's disambiguation window.
    await waitFor(() => expect(onExit).toHaveBeenCalledTimes(1));
    expect(onNew).not.toHaveBeenCalled();
  });

  it("has no esc hint at launch, where there is nothing to go back to", async () => {
    const { stdout } = await mount(makeClient());
    await waitFor(() => expect(stdout.lastFrame()).toContain("enter open"));
    expect(stdout.lastFrame()).not.toContain("esc back");
  });

  it("pins through update_thread and refetches", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.press("p");
    await settle();
    expect(stub.updateThread).toHaveBeenCalledWith("a", { pinned: true });
    expect(stub.listThreads).toHaveBeenCalledTimes(2);
  });

  it("archives through update_thread", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.press("a");
    await settle();
    expect(stub.updateThread).toHaveBeenCalledWith("a", { status: "archived" });
  });

  it("forks and opens the child rather than returning to the list", async () => {
    const stub = makeClient();
    const onOpen = vi.fn();
    const { stdin } = await mount(stub, { onOpen });
    stdin.press("f");
    await settle();
    expect(stub.forkSession).toHaveBeenCalledWith("a");
    expect(onOpen).toHaveBeenCalledWith("forked-1");
  });

  it("places a retention hold", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.press("h");
    await settle();
    expect(stub.setSessionHold).toHaveBeenCalledWith("a", true);
  });

  it("requires a confirmation before deleting", async () => {
    const stub = makeClient();
    const { stdin, stdout } = await mount(stub);
    stdin.press("d");
    await settle();
    expect(stub.deleteSession).not.toHaveBeenCalled();
    expect(stdout.lastFrame()).toContain("cannot be undone");
    stdin.press("y");
    await settle();
    expect(stub.deleteSession).toHaveBeenCalledWith("a");
  });

  it("abandons a delete on n", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.press("d");
    await settle();
    stdin.press("n");
    await settle();
    expect(stub.deleteSession).not.toHaveBeenCalled();
  });

  it("declines organize keys on the degraded surface instead of calling", async () => {
    const stub = makeClient([]);
    const { stdin, stdout } = await mount(stub);
    stdin.press("p");
    await settle();
    expect(stub.updateThread).not.toHaveBeenCalled();
    expect(stdout.lastFrame()).toContain("does not support organizing");
  });
});

describe("SessionManager search", () => {
  it("delegates to search_threads when granted", async () => {
    const stub = makeClient();
    const { stdin, stdout } = await mount(stub);
    stdin.press("/");
    await settle();
    for (const ch of "abc") stdin.press(ch);
    await settle();
    stdin.press(KEY.enter);
    await settle();
    expect(stub.searchThreads).toHaveBeenCalledWith("abc", { limit: 100 });
    expect(stdout.lastFrame()).toContain("match");
  });

  it("filters locally when search is not granted", async () => {
    const stub = makeClient([]);
    const { stdin } = await mount(stub);
    stdin.press("/");
    await settle();
    stdin.press("z");
    await settle();
    stdin.press(KEY.enter);
    await settle();
    expect(stub.searchThreads).not.toHaveBeenCalled();
    // Re-read the stable surface, then narrow it in memory.
    expect(stub.listSessions).toHaveBeenCalledTimes(2);
  });

  it("abandons the query on escape and hands the keyboard back", async () => {
    const stub = makeClient();
    const onOpen = vi.fn();
    const { stdin } = await mount(stub, { onOpen });
    stdin.press("/");
    await settle();
    stdin.press("q");
    await settle();
    stdin.press(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(stub.searchThreads).not.toHaveBeenCalled();
    // Proves escape actually left search mode rather than doing nothing:
    // Enter now opens the selection instead of submitting a query.
    stdin.press(KEY.enter);
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
    stdin.press(KEY.down + KEY.enter);
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith("b"));
  });
  it("does not treat pasted commands as actions", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    for (const command of ["p", "a", "f", "d"])
      stdin.press(`\x1b[200~${command}\x1b[201~`);
    await settle();
    expect(stub.updateThread).not.toHaveBeenCalled();
    expect(stub.forkSession).not.toHaveBeenCalled();
    expect(stub.deleteSession).not.toHaveBeenCalled();
  });
  it("reads a whole rename-and-submit burst without dropping characters", async () => {
    const stub = makeClient();
    const { stdin } = await mount(stub);
    stdin.press("r!" + KEY.enter);
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
    stdin.press("ff" + KEY.tab);
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
  stdin.press("r");
  await settle();
  stub.listThreads.mockResolvedValue({ threads: [thread("b")] });
  stub.events.emit("thread_updated", {});
  await settle();
  stdin.press("!" + KEY.enter);
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
  const { stdin, stdout } = await mount(stub);
  stdin.press("/match" + KEY.enter);
  await settle();
  resolve({ threads: [thread("a")] });
  await settle();
  expect(stdout.lastFrame()).toContain("Thread b");
  expect(stdout.lastFrame()).not.toContain("Thread a");
});

it("does not reuse an old delete confirmation during a cancel/select/delete/yes burst", async () => {
  const stub = makeClient();
  const { stdin } = await mount(stub);
  stdin.press("d");
  await settle();
  stdin.press("n" + KEY.down + "dy");
  await settle();
  expect(stub.deleteSession).not.toHaveBeenCalled();
  stdin.press("y");
  await waitFor(() => expect(stub.deleteSession).toHaveBeenCalledWith("b"));
});

it("re-reads the list after a lost fork reply instead of replaying or warning", async () => {
  const stub = makeClient();
  stub.forkSession.mockRejectedValue(new ConnectionClosedError("lost reply"));
  const { stdin, stdout } = await mount(stub);
  const listsBefore = stub.listThreads.mock.calls.length;
  stdin.press("f");
  await settle();
  expect(stdout.lastFrame()).not.toContain("forking");
  stub.events.emit("rpc_ready");
  await settle();
  expect(stub.forkSession).toHaveBeenCalledTimes(1);
  expect(stub.listThreads.mock.calls.length).toBeGreaterThan(listsBefore);
  expect(stdout.lastFrame()).not.toContain("outcome unknown");
  expect(stdout.lastFrame()).not.toContain("fork failed");
});

it("refetches immediately after a lost mutation reply when already reconnected", async () => {
  const stub = makeClient();
  (stub as { initializeResult?: unknown }).initializeResult = {};
  stub.updateThread.mockRejectedValueOnce(new ConnectionClosedError("lost"));
  const { stdin, stdout } = await mount(stub);
  const listsBefore = stub.listThreads.mock.calls.length;
  stdin.press("a");
  await waitFor(() =>
    expect(stub.listThreads.mock.calls.length).toBeGreaterThan(listsBefore),
  );
  await settle();
  expect(stdout.lastFrame()).not.toContain("outcome unknown");
  expect(stdout.lastFrame()).not.toContain("failed");
});
