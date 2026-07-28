import WebSocket from "ws";
import { describe, expect, it } from "vitest";
import { JSONRPCError, RPCClient } from "./client.js";
import type { ClientRequest } from "../types/protocol.js";

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
