import WebSocket from "ws";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JSONRPCError, RPCClient } from "./client.js";
import {
  ConnectionClosedError,
  DEFAULT_LIFECYCLE_POLICY,
  RpcAbortError,
  RpcTimeoutError,
} from "./lifecycle.js";
import type { ClientRequest } from "../types/protocol.js";
import { REQUESTED_EXPERIMENTAL_OPERATIONS } from "../protocol/experimental.js";

const { mockSockets, mockSocketConfig } = vi.hoisted(() => ({
  mockSockets: [] as Array<{
    sent: string[];
    emit: (event: string, ...values: unknown[]) => void;
  }>,
  mockSocketConfig: {
    failConnects: false,
    serverVersion: '2.0.0',
    incompleteHandshake: false,
    earlyNotification: false,
    // One-shot (or persistent, below): the next initialize is rejected
    // with the typed capability_not_negotiated error naming these
    // operations.
    rejectCapabilities: null as string[] | null,
    rejectCapabilitiesPersistent: false,
  },
}));

vi.mock("ws", () => {
  class MockWebSocket {
    static readonly OPEN = 1;
    readonly readyState = MockWebSocket.OPEN;
    readonly sent: string[] = [];
    private listeners = new Map<
      string,
      Array<(...values: unknown[]) => void>
    >();

    constructor(readonly url: string) {
      mockSockets.push(this);
      if (mockSocketConfig.failConnects) {
        queueMicrotask(() => this.emit("close", 1006));
      } else {
        queueMicrotask(() => this.emit("open"));
      }
    }

    on(event: string, listener: (...values: unknown[]) => void): void {
      const listeners = this.listeners.get(event) ?? [];
      listeners.push(listener);
      this.listeners.set(event, listeners);
    }

    emit(event: string, ...values: unknown[]): void {
      for (const listener of this.listeners.get(event) ?? [])
        listener(...values);
    }

    send(data: string, callback?: (error?: Error) => void): void {
      this.sent.push(data);
      callback?.();
      const request = JSON.parse(data) as {
        id?: number;
        method: string;
        params?: { capabilities?: { experimental_operations?: string[] } };
      };
      if (request.method === "initialize") {
        if (mockSocketConfig.earlyNotification) {
          queueMicrotask(() =>
            this.emit(
              "message",
              JSON.stringify({
                jsonrpc: "2.0",
                method: "message_output",
                params: { content: "untrusted" },
              }),
            ),
          );
        }
        const rejected = mockSocketConfig.rejectCapabilities;
        if (rejected) {
          if (!mockSocketConfig.rejectCapabilitiesPersistent) {
            mockSocketConfig.rejectCapabilities = null;
          }
          queueMicrotask(() =>
            this.emit(
              "message",
              JSON.stringify({
                jsonrpc: "2.0",
                id: request.id,
                error: {
                  code: -32013,
                  message: "Unsupported experimental capabilities",
                  data: {
                    kind: "capability_not_negotiated",
                    unsupported_capabilities: rejected,
                  },
                },
              }),
            ),
          );
          return;
        }
        // Like the real server: negotiated grants echo the request, sorted.
        const requested =
          request.params?.capabilities?.experimental_operations ?? [];
        queueMicrotask(() =>
          this.emit(
            "message",
            JSON.stringify({
              jsonrpc: "2.0",
              id: request.id,
              result: {
                protocol_version: mockSocketConfig.serverVersion,
                identity: { name: "omniagents", version: "1.2.3" },
                platform: { os: "linux", arch: "x86_64" },
                capabilities: {
                  realtime: false,
                  mcp_apps: false,
                  client_functions: true,
                  approvals: true,
                  artifacts: false,
                  replay: true,
                  terminal: true,
                  experimental_operations: [...requested].sort(),
                  disabled_notifications: [],
                },
                agent_host: mockSocketConfig.incompleteHandshake
                  ? null
                  : {
                      agent_host_id: "agent-host-1",
                      default_workspace_id: "workspace-1",
                      default_environment_id: "environment-1",
                    },
              },
            }),
          ),
        );
      }
    }

    ping(): void {
      this.emit("pong");
    }
    terminate(): void {
      this.emit("close", 1006);
    }
    close(): void {}
  }

  return { default: MockWebSocket };
});

type TestClient = {
  ws: {
    readyState: number;
    send: (data: string, callback: (error?: Error) => void) => void;
  };
  handleMessage: (message: unknown) => void;
  on: RPCClient["on"];
  listSessions: RPCClient["listSessions"];
  archiveSession: RPCClient["archiveSession"];
  deleteSession: RPCClient["deleteSession"];
  registerClientFunction: RPCClient["registerClientFunction"];
  serverCall: RPCClient["serverCall"];
  sendUserMessage: RPCClient["sendUserMessage"];
  forkSession: RPCClient["forkSession"];
  listThreads: RPCClient["listThreads"];
  mcpCallTool: RPCClient["mcpCallTool"];
  getConfig: RPCClient["getConfig"];
  gitStatus: RPCClient["gitStatus"];
  gitStage: RPCClient["gitStage"];
  gitDiscard: RPCClient["gitDiscard"];
};

function connectedClient(sent: string[]): TestClient {
  const client = new RPCClient({
    url: "ws://example.test/ws",
  }) as unknown as TestClient;
  client.ws = {
    readyState: WebSocket.OPEN,
    send(data, callback) {
      sent.push(data);
      callback();
    },
  };
  return client;
}

describe("RPCClient", () => {
  it('does not deliver events before the handshake is accepted', async () => {
    mockSocketConfig.earlyNotification = true;
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const output = vi.fn();
    client.on('message_output', output);
    await client.connect();
    expect(output).not.toHaveBeenCalled();
    client.disconnect();
  });
  it.each(['1.0.0', '3.0.0', 'invalid', '2.0.0-dev', '02.0.0', '', '2.0.0\n'])('rejects a successful but incompatible %s handshake', async (version) => {
    mockSocketConfig.serverVersion = version;
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const ready = vi.fn();
    client.on("rpc_ready", ready);
    await expect(client.connect()).rejects.toMatchObject({ code: -32012 });
    expect(ready).not.toHaveBeenCalled();
    expect(client.initializeResult).toBeNull();
    expect(mockSockets).toHaveLength(1);
    expect(mockSockets[0]!.sent.map((value) => JSON.parse(value).method)).toEqual(['initialize']);
    client.disconnect();
  });

  it('rejects an incomplete successful handshake before initialized', async () => {
    mockSocketConfig.incompleteHandshake = true;
    const client = new RPCClient({ url: "ws://example.test/ws" });
    await expect(client.connect()).rejects.toMatchObject({ code: -32602 });
    expect(client.initializeResult).toBeNull();
    expect(mockSockets[0]!.sent.map((value) => JSON.parse(value).method)).toEqual(['initialize']);
    client.disconnect();
  });

  it.each(['2.0.1', '2.1.0'])('accepts compatible newer server %s', async (version) => {
    mockSocketConfig.serverVersion = version;
    const client = new RPCClient({ url: "ws://example.test/ws" });
    await client.connect();
    expect(client.initializeResult?.protocol_version).toBe(version);
    client.disconnect();
  });

  it("cancels queued reconnects on explicit disconnect", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({ url: "ws://example.test/ws" });
    await client.connect();
    mockSockets[0]!.emit("close", 1006);
    client.disconnect();
    await vi.advanceTimersByTimeAsync(2000);
    expect(mockSockets).toHaveLength(1);
  });

  it("cleans deadlines and abort listeners on successful responses", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({ url: "ws://example.test/ws" });
    await client.connect();
    const abort = new AbortController();
    const remove = vi.spyOn(abort.signal, "removeEventListener");
    const promise = client.serverCall("probe", {}, undefined, {
      signal: abort.signal,
    });
    const request = JSON.parse(mockSockets[0]!.sent.at(-1)!);
    mockSockets[0]!.emit(
      "message",
      JSON.stringify({ jsonrpc: "2.0", id: request.id, result: {} }),
    );
    await promise;
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    client.disconnect();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("terminates a socket that stops answering heartbeat pings", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({
      url: "ws://example.test/ws",
      policy: {
        ...DEFAULT_LIFECYCLE_POLICY,
        pingIntervalMs: 100,
        pingTimeoutMs: 50,
      },
    });
    await client.connect();
    const socket = mockSockets[0] as any;
    socket.ping = vi.fn();
    const terminate = vi.spyOn(socket, "terminate");
    await vi.advanceTimersByTimeAsync(151);
    expect(terminate).toHaveBeenCalledOnce();
    client.disconnect();
  });

  it("retains the exact client response after a send failure without re-execution", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const handler = vi.fn(async () => ({ ack: true }));
    client.registerClientFunction("probe", handler);
    const send = vi
      .spyOn(client, "clientResponse")
      .mockRejectedValueOnce(new Error("disconnected"))
      .mockResolvedValue(undefined);
    const failed = vi.fn();
    client.on("client_response_failed", failed);
    const event = {
      jsonrpc: "2.0",
      method: "client_request",
      params: { request_id: "r", function: "probe", args: {} },
    };
    (client as any).handleMessage(event);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(failed).toHaveBeenCalledOnce();
    (client as any).handleMessage(event);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(handler).toHaveBeenCalledOnce();
    expect(send).toHaveBeenNthCalledWith(
      2,
      "r",
      true,
      { ack: true },
      undefined,
    );
    expect((client as any).responseOutbox.size).toBe(0);
  });

  it("hydrates at a snapshot cursor, restores prompts and applies later events once", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    vi.spyOn(client, "isGranted").mockReturnValue(true);
    let snapshot!: (value: unknown) => void;
    vi.spyOn(client as any, "call").mockImplementation(
      () =>
        new Promise((resolve) => {
          snapshot = resolve;
        }),
    );
    const replay = vi
      .spyOn(client, "resumeSession")
      .mockResolvedValue({
        stream_id: "epoch",
        last_seq: 11,
        events: [
          {
            method: "run_end",
            params: {
              session_id: "s",
              stream_id: "epoch",
              seq: 11,
              run_id: "run",
            },
          },
        ],
      });
    const ended = vi.fn();
    const approval = vi.fn();
    client.on("run_end", ended);
    client.on("tool_approval_requested", approval);
    const apply = vi.fn();
    const syncing = client.synchronizeSession("s", apply);
    (client as any).handleMessage({
      jsonrpc: "2.0",
      method: "run_end",
      params: { session_id: "s", stream_id: "epoch", seq: 11, run_id: "run" },
    });
    expect(ended).not.toHaveBeenCalled();
    snapshot({
      run_active: true,
      active_run_id: "run",
      snapshot: {
        stream_id: "epoch",
        last_seq: 10,
        queue: [],
        items: [
          {
            kind: "approval",
            status: "started",
            turn_id: "run",
            content: {
              approval_kind: "tool",
              call_id: "call",
              tool: "bash",
              arguments: "{}",
            },
          },
        ],
      },
    });
    await syncing;
    expect(apply).toHaveBeenCalledOnce();
    expect(approval).toHaveBeenCalledOnce();
    expect(replay).toHaveBeenCalledWith("s", "epoch", 10);
    expect(ended).toHaveBeenCalledOnce();
    client.disconnect();
  });

  it("does not apply hydration after the selected session changes", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    vi.spyOn(client, "isGranted").mockReturnValue(true);
    let resolve!: (value: unknown) => void;
    vi.spyOn(client as any, "call").mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    let current = true;
    const apply = vi.fn();
    const syncing = client.synchronizeSession("s", apply, () => current);
    current = false;
    resolve({});
    await syncing;
    expect(apply).not.toHaveBeenCalled();
  });

  it("restarts hydration at a new epoch when replay requires resync", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    vi.spyOn(client, "isGranted").mockReturnValue(true);
    const snapshot = (stream_id: string, last_seq: number) => ({
      run_active: false,
      active_run_id: null,
      snapshot: { stream_id, last_seq, items: [], queue: [] },
    });
    vi.spyOn(client as any, "call")
      .mockResolvedValueOnce(snapshot("old", 90))
      .mockResolvedValueOnce(snapshot("new", 1));
    vi.spyOn(client, "resumeSession")
      .mockRejectedValueOnce(
        new JSONRPCError({ code: -32030, message: "epoch changed" }),
      )
      .mockResolvedValueOnce({
        stream_id: "new",
        last_seq: 2,
        events: [
          {
            method: "run_end",
            params: {
              session_id: "s",
              stream_id: "new",
              seq: 2,
              run_id: "run",
            },
          },
        ],
      });
    const ended = vi.fn();
    client.on("run_end", ended);
    await client.synchronizeSession("s", () => {});
    expect(ended).toHaveBeenCalledOnce();
    expect((client as any).replay.tracker("s").lastSeq).toBe(2);
    client.disconnect();
  });

  it("uses stable reads when snapshot capability is absent and retries raced reads", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const call = vi.spyOn(client as any, "call");
    const history = vi.spyOn(client, "getSessionHistory").mockResolvedValue([]);
    vi.spyOn(client, "listAllItems").mockResolvedValue([]);
    vi.spyOn(client, "listQueue").mockResolvedValue({
      session_id: "s",
      depth: 0,
      items: [],
    });
    vi.spyOn(client, "resumeSession")
      .mockResolvedValueOnce({ stream_id: "epoch", last_seq: 10, events: [] })
      .mockResolvedValue({ stream_id: "epoch", last_seq: 11, events: [] });
    const apply = vi.fn();
    await client.synchronizeSession("s", apply);
    expect(history).toHaveBeenCalledTimes(2);
    expect(call).not.toHaveBeenCalled();
    expect(apply.mock.calls[0]![0].snapshot.last_seq).toBe(11);
    client.disconnect();
  });

  it("leaves another session's client requests pending instead of acknowledging them", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    client.setActiveSession("selected");
    const handler = vi.fn(async () => ({}));
    client.registerClientFunction("escalate", handler);
    const response = vi.spyOn(client, "clientResponse");
    (client as any).handleMessage({
      jsonrpc: "2.0",
      method: "client_request",
      params: {
        session_id: "other",
        request_id: "r",
        function: "escalate",
        args: {},
      },
    });
    await Promise.resolve();
    expect(handler).not.toHaveBeenCalled();
    expect(response).not.toHaveBeenCalled();
  });

  it("rejects an in-flight connect when explicitly disconnected before open", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const connecting = client.connect();
    client.disconnect();
    await expect(connecting).rejects.toBeInstanceOf(ConnectionClosedError);
  });

  it("isolates a new empty session and buffers its early run events until its id is known", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    client.setActiveSession(undefined);
    let finish!: (value: unknown) => void;
    vi.spyOn(client, "request").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const started = vi.fn();
    client.on("run_started", started);
    const event = (session_id: string) => ({ jsonrpc: "2.0", method: "run_started", params: { session_id, run_id: session_id } });
    (client as any).handleMessage(event("old"));
    const run = client.startRun("hello", { mode: "none" });
    (client as any).handleMessage(event("old"));
    (client as any).handleMessage(event("new"));
    expect(started).not.toHaveBeenCalled();
    finish({ session_id: "new", run_id: "new" });
    await run;
    expect(started).toHaveBeenCalledOnce();
    expect(started).toHaveBeenCalledWith({ session_id: "new", run_id: "new" });
  });

  afterEach(() => {
    mockSockets.length = 0;
    mockSocketConfig.failConnects = false;
    mockSocketConfig.serverVersion = '2.0.0';
    mockSocketConfig.incompleteHandshake = false;
    mockSocketConfig.earlyNotification = false;
    mockSocketConfig.rejectCapabilities = null;
    mockSocketConfig.rejectCapabilitiesPersistent = false;
    vi.useRealTimers();
  });

  it("handshakes before ready and handshakes again after reconnect", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const ready = vi.fn();
    client.on("rpc_ready", ready);

    const connected = client.connect();
    await vi.runAllTicks();
    await connected;

    expect(mockSockets[0]!.sent.map((message) => JSON.parse(message))).toEqual([
      {
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocol_version: "2.0.0",
          identity: { name: "omniagents-ink", version: "0.1.0" },
          platform: { os: process.platform, arch: process.arch },
          capabilities: {
            realtime: false,
            mcp_apps: false,
            client_functions: true,
            approvals: true,
            artifacts: false,
            replay: true,
            terminal: true,
            experimental_operations: [...REQUESTED_EXPERIMENTAL_OPERATIONS],
            disabled_notifications: [],
          },
        },
      },
      { jsonrpc: "2.0", method: "initialized", params: {} },
    ]);
    expect(ready).toHaveBeenCalledTimes(1);
    expect(client.initializeResult?.protocol_version).toBe("2.0.0");
    expect(client.defaultEnvironmentSelection()).toEqual({
      mode: "explicit",
      environment_id: "environment-1",
    });

    mockSockets[0]!.emit("close");
    await vi.advanceTimersByTimeAsync(1000);
    await vi.runAllTicks();

    expect(mockSockets).toHaveLength(2);
    expect(JSON.parse(mockSockets[1]!.sent[0]!)).toMatchObject({
      id: 2,
      method: "initialize",
    });
    expect(JSON.parse(mockSockets[1]!.sent[1]!)).toEqual({
      jsonrpc: "2.0",
      method: "initialized",
      params: {},
    });
    expect(ready).toHaveBeenCalledTimes(2);
  });

  it("is granted the requested experimental operations at the handshake", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const connected = client.connect();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await connected;

    expect(client.isGranted("git_status")).toBe(true);
    expect(client.isGranted("item_updated")).toBe(true);
    // Stable operations never appear in the grant list.
    expect(client.isGranted("start_run")).toBe(false);
    // Launcher-privileged control plane is never requested.
    expect(client.isGranted("agent_host_register_workspace")).toBe(false);
    expect(client.grantedExperimentalOperations).toEqual(
      [...REQUESTED_EXPERIMENTAL_OPERATIONS].sort(),
    );
    expect(client.defaultEnvironmentId()).toBe("environment-1");
  });

  it("strips capabilities an older server rejects and retries once", async () => {
    vi.useFakeTimers();
    mockSocketConfig.rejectCapabilities = ["git_push", "item_updated"];
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const ready = vi.fn();
    client.on("rpc_ready", ready);

    const connected = client.connect();
    await vi.runAllTicks();
    await connected;

    const inits = mockSockets[0]!.sent
      .map((message) => JSON.parse(message))
      .filter((message) => message.method === "initialize");
    expect(inits).toHaveLength(2);
    expect(inits[0]!.params.capabilities.experimental_operations).toContain(
      "git_push",
    );
    const retried = inits[1]!.params.capabilities
      .experimental_operations as string[];
    expect(retried).not.toContain("git_push");
    expect(retried).not.toContain("item_updated");
    expect(retried).toContain("git_status");
    expect(ready).toHaveBeenCalledTimes(1);
    expect(client.isGranted("git_push")).toBe(false);
    expect(client.isGranted("git_status")).toBe(true);

    // The reduction is remembered: the reconnect handshake negotiates
    // cleanly on its first attempt.
    mockSockets[0]!.emit("close");
    await vi.advanceTimersByTimeAsync(1000);
    await vi.runAllTicks();

    const reconnectInits = mockSockets[1]!.sent
      .map((message) => JSON.parse(message))
      .filter((message) => message.method === "initialize");
    expect(reconnectInits).toHaveLength(1);
    expect(
      reconnectInits[0]!.params.capabilities.experimental_operations,
    ).not.toContain("git_push");
  });

  it("fails permanently when the capability rejection survives the retry", async () => {
    vi.useFakeTimers();
    mockSocketConfig.rejectCapabilities = ["git_push"];
    mockSocketConfig.rejectCapabilitiesPersistent = true;
    const client = new RPCClient({ url: "ws://example.test/ws" });

    const connected = client.connect();
    await vi.runAllTicks();
    await expect(connected).rejects.toMatchObject({
      name: "JSONRPCError",
      code: -32013,
    });

    // Deterministic handshake rejection: no reconnect loop.
    await vi.advanceTimersByTimeAsync(60000);
    expect(mockSockets).toHaveLength(1);
  });

  it("re-emits protocol notifications under their method name", () => {
    const client = connectedClient([]);
    const itemUpdated = vi.fn();
    const accountChanged = vi.fn();
    const fsEvents = vi.fn();
    const requestResolved = vi.fn();
    client.on("item_updated", itemUpdated);
    client.on("account_changed", accountChanged);
    client.on("fs_events", fsEvents);
    client.on("client_request_resolved", requestResolved);

    client.handleMessage({
      jsonrpc: "2.0",
      method: "item_updated",
      params: {
        session_id: "sess",
        thread_id: "thread-1",
        item_id: "item-1",
        kind: "message",
        status: "completed",
        revision: 1,
        item_seq: 1,
        content: {},
      },
    });
    client.handleMessage({
      jsonrpc: "2.0",
      method: "account_changed",
      params: { provider: "anthropic", reason: "login" },
    });
    client.handleMessage({
      jsonrpc: "2.0",
      method: "fs_events",
      params: { environment_id: "environment-1", watch_id: "w1", events: [] },
    });
    client.handleMessage({
      jsonrpc: "2.0",
      method: "client_request_resolved",
      params: { request_id: "request-1" },
    });

    expect(itemUpdated).toHaveBeenCalledWith(
      expect.objectContaining({ item_id: "item-1" }),
    );
    expect(accountChanged).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "anthropic" }),
    );
    expect(fsEvents).toHaveBeenCalledWith(
      expect.objectContaining({ watch_id: "w1" }),
    );
    expect(requestResolved).toHaveBeenCalledWith(
      expect.objectContaining({ request_id: "request-1" }),
    );
  });

  it("omits params when listing sessions", async () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    const response = client.listSessions();
    const request = JSON.parse(sent[0]!) as Record<string, unknown>;
    client.handleMessage({ jsonrpc: "2.0", id: request.id, result: [] });

    await expect(response).resolves.toEqual([]);
    expect(request).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "list_sessions",
    });
  });

  it("preserves structured JSON-RPC errors", async () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    const response = client.archiveSession("session-1");
    const request = JSON.parse(sent[0]!) as Record<string, unknown>;
    client.handleMessage({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32001,
        message: "Session is busy",
        data: { session_id: "session-1" },
      },
    });

    await expect(response).rejects.toMatchObject({
      name: "JSONRPCError",
      code: -32001,
      message: "Session is busy",
      data: { session_id: "session-1" },
    } satisfies Partial<JSONRPCError>);
  });

  it("tolerates unknown notifications", () => {
    const client = connectedClient([]);

    expect(() =>
      client.handleMessage({
        jsonrpc: "2.0",
        method: "future_notification",
        params: { enabled: true },
      }),
    ).not.toThrow();
  });

  it("correlates client_request notifications without a JSON-RPC id", async () => {
    const sent: string[] = [];
    const client = connectedClient(sent);
    client.registerClientFunction("ui.echo", async ({ value }) => ({ value }));
    const notification: ClientRequest = {
      jsonrpc: "2.0",
      method: "client_request",
      params: {
        request_id: "request-1",
        function: "ui.echo",
        args: { value: "ok" },
      },
    };

    client.handleMessage(notification);
    await new Promise((resolve) => setTimeout(resolve, 0));

    const response = JSON.parse(sent[0]!) as Record<string, unknown>;
    expect(response).toMatchObject({
      jsonrpc: "2.0",
      method: "client_response",
      params: {
        request_id: "request-1",
        ok: true,
        result: { value: "ok" },
      },
    });
  });

  it("rejects every pending call when the connection closes permanently", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const failed = vi.fn();
    client.on("reconnect_failed", failed);
    const connected = client.connect();
    await vi.runAllTicks();
    await connected;

    const call = client.request("server_call", { function: "slow" });
    const rejection = expect(call).rejects.toMatchObject({
      name: "ConnectionClosedError",
      permanent: true,
      closeCode: 4403,
    });
    mockSockets[0]!.emit("close", 4403, Buffer.from("auth required"));
    await rejection;

    // Permanent close: no reconnect, terminal event emitted.
    await vi.advanceTimersByTimeAsync(60000);
    expect(mockSockets).toHaveLength(1);
    expect(failed).toHaveBeenCalledWith({ permanent: true, attempts: 1 });
  });

  it("stops reconnecting when the retry budget is exhausted", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({
      url: "ws://example.test/ws",
      policy: {
        ...DEFAULT_LIFECYCLE_POLICY,
        reconnectInitialDelayMs: 10,
        reconnectMaxDelayMs: 50,
        reconnectJitter: 0,
        reconnectMaxAttempts: 2,
      },
    });
    const reconnecting = vi.fn();
    const failed = vi.fn();
    client.on("reconnecting", reconnecting);
    client.on("reconnect_failed", failed);
    const connected = client.connect();
    await vi.runAllTicks();
    await connected;

    // Every reconnect attempt dies before the handshake, so the failure
    // counter never resets and the budget (2) runs out.
    mockSocketConfig.failConnects = true;
    mockSockets[0]!.emit("close", 1006);
    await vi.advanceTimersByTimeAsync(60000);

    expect(mockSockets).toHaveLength(2);
    expect(reconnecting).toHaveBeenCalledTimes(1);
    expect(reconnecting).toHaveBeenCalledWith(1);
    expect(failed).toHaveBeenCalledWith({ permanent: true, attempts: 2 });
  });

  it("times out unanswered calls using the policy deadline", async () => {
    vi.useFakeTimers();
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const connected = client.connect();
    await vi.runAllTicks();
    await connected;

    const call = client.request("server_call", { function: "slow" });
    call.catch(() => {});
    await vi.advanceTimersByTimeAsync(DEFAULT_LIFECYCLE_POLICY.rpcTimeoutMs);

    await expect(call).rejects.toMatchObject({
      name: "RpcTimeoutError",
      method: "server_call",
      timeoutMs: DEFAULT_LIFECYCLE_POLICY.rpcTimeoutMs,
    } satisfies Partial<RpcTimeoutError>);
  });

  it("aborts long calls via AbortSignal", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const connected = client.connect();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await connected;

    const controller = new AbortController();
    const call = client.request(
      "server_call",
      { function: "slow" },
      { signal: controller.signal },
    );
    controller.abort();

    await expect(call).rejects.toBeInstanceOf(RpcAbortError);
  });

  it("rejects pending calls on deliberate disconnect", async () => {
    const client = new RPCClient({ url: "ws://example.test/ws" });
    const connected = client.connect();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await connected;

    const call = client.request("server_call", { function: "slow" });
    client.disconnect();

    await expect(call).rejects.toBeInstanceOf(ConnectionClosedError);
    await expect(call).rejects.toMatchObject({ permanent: true });
    // Deliberate close never triggers a reconnect loop.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mockSockets).toHaveLength(1);
  });
});

describe("RPCClient typed git wrappers", () => {
  it("omits unset git options instead of sending nulls", async () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.gitStatus("sess", "apps/api");
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "git_status",
      params: { environment_id: "sess", repo: "apps/api" },
    });
  });

  it("sends hunk selections verbatim", async () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.gitStage("sess", ".", {
      hunks: [{ path: "src/app.ts", hunk_id: "deadbeefdeadbeef" }],
    });
    expect(JSON.parse(sent[0]!)).toMatchObject({
      method: "git_stage",
      params: {
        environment_id: "sess",
        repo: ".",
        hunks: [{ path: "src/app.ts", hunk_id: "deadbeefdeadbeef" }],
      },
    });
  });

  it("preserves the typed confirmation error for destructive operations", async () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    const response = client.gitDiscard("sess", ".", { paths: ["a.txt"] });
    const request = JSON.parse(sent[0]!) as Record<string, unknown>;
    client.handleMessage({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32093,
        message: "discard_changes discards work and must be confirmed explicitly",
        data: {
          kind: "git_confirmation_required",
          operation: "discard_changes",
          confirmation_token: "tok",
          impact: { paths: ["a.txt"] },
        },
      },
    });

    await expect(response).rejects.toMatchObject({
      code: -32093,
      data: { kind: "git_confirmation_required", confirmation_token: "tok" },
    });
  });
});

describe("RPCClient session control, config & thread wrappers", () => {
  it("shapes fork_session params in snake_case, omitting unset options", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.forkSession("sess", {
      newSessionId: "sess-2",
      fromItemId: "item-9",
    });
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "fork_session",
      params: {
        session_id: "sess",
        new_session_id: "sess-2",
        from_item_id: "item-9",
      },
    });
  });

  it("sends steering messages against the active run", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.sendUserMessage("run-1", "also check the tests");
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "send_user_message",
      params: { run_id: "run-1", content: "also check the tests" },
    });
  });

  it("sends delete_session cascade only when set", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.deleteSession("sess");
    void client.deleteSession("sess", true);
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "delete_session",
      params: { session_id: "sess" },
    });
    expect(JSON.parse(sent[1]!)).toMatchObject({
      method: "delete_session",
      params: { session_id: "sess", cascade: true },
    });
  });

  it("omits unset thread filters and keeps false values", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.listThreads({ pinned: false, limit: 10 });
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "list_threads",
      params: { pinned: false, limit: 10 },
    });
  });

  it("sends get_config without params", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.getConfig();
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "get_config",
    });
  });

  it("shapes mcp_call_tool params", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.mcpCallTool("github", "search_issues", "sess", {
      query: "is:open",
    });
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "mcp_call_tool",
      params: {
        server_name: "github",
        tool_name: "search_issues",
        session_id: "sess",
        args: { query: "is:open" },
      },
    });
  });

  it("routes server_call through an environment when asked", () => {
    const sent: string[] = [];
    const client = connectedClient(sent);

    void client.serverCall("goal.status", {}, "sess", {
      environmentId: "environment-1",
      environmentGeneration: 3,
    });
    expect(JSON.parse(sent[0]!)).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "server_call",
      params: {
        function: "goal.status",
        args: {},
        session_id: "sess",
        environment_id: "environment-1",
        environment_generation: 3,
      },
    });
  });
});
