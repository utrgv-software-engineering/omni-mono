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

const { mockSockets, mockSocketConfig } = vi.hoisted(() => ({
  mockSockets: [] as Array<{
    sent: string[];
    emit: (event: string, ...values: unknown[]) => void;
  }>,
  mockSocketConfig: { failConnects: false },
}));

vi.mock("ws", () => {
  class MockWebSocket {
    static readonly OPEN = 1;
    readonly readyState = MockWebSocket.OPEN;
    readonly sent: string[] = [];
    private listeners = new Map<string, Array<(...values: unknown[]) => void>>();

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
      const request = JSON.parse(data) as { id?: number; method: string };
      if (request.method === "initialize") {
        queueMicrotask(() =>
          this.emit(
            "message",
            JSON.stringify({
              jsonrpc: "2.0",
              id: request.id,
              result: {
                protocol_version: "1.0.0",
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
                  experimental_operations: [],
                  disabled_notifications: [],
                },
                agent_host: {
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
  listSessions: RPCClient["listSessions"];
  archiveSession: RPCClient["archiveSession"];
  registerClientFunction: RPCClient["registerClientFunction"];
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
  afterEach(() => {
    mockSockets.length = 0;
    mockSocketConfig.failConnects = false;
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
          protocol_version: "1.0.0",
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
            experimental_operations: [],
            disabled_notifications: [],
          },
        },
      },
      { jsonrpc: "2.0", method: "initialized", params: {} },
    ]);
    expect(ready).toHaveBeenCalledTimes(1);
    expect(client.initializeResult?.protocol_version).toBe("1.0.0");
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
