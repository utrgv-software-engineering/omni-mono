import WebSocket from "ws";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JSONRPCError, RPCClient } from "./client.js";
import type { ClientRequest } from "../types/protocol.js";

const { mockSockets } = vi.hoisted(() => ({
  mockSockets: [] as Array<{
    sent: string[];
    emit: (event: string, value?: unknown) => void;
  }>,
}));

vi.mock("ws", () => {
  class MockWebSocket {
    static readonly OPEN = 1;
    readonly readyState = MockWebSocket.OPEN;
    readonly sent: string[] = [];
    private listeners = new Map<string, Array<(value?: unknown) => void>>();

    constructor(readonly url: string) {
      mockSockets.push(this);
      queueMicrotask(() => this.emit("open"));
    }

    on(event: string, listener: (value?: unknown) => void): void {
      const listeners = this.listeners.get(event) ?? [];
      listeners.push(listener);
      this.listeners.set(event, listeners);
    }

    emit(event: string, value?: unknown): void {
      for (const listener of this.listeners.get(event) ?? []) listener(value);
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
});
