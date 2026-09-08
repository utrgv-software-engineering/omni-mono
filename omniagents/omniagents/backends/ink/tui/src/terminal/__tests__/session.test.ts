import { createServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WebSocketServer, type WebSocket as ServerSocket } from "ws";
import { terminalSession } from "../session.js";
import type { RPCClient } from "../../rpc/client.js";

const settle = (ms = 60) => new Promise((resolve) => setTimeout(resolve, ms));

type Frame = Record<string, unknown>;

function b64(text: string): string {
  return Buffer.from(text, "utf8").toString("base64");
}

describe("terminalSession end-to-end against a frame-accurate server", () => {
  let server: WebSocketServer | null = null;

  afterEach(() => {
    terminalSession.close();
    server?.close();
    server = null;
  });

  it("cancels creation on close and releases the late terminal without opening a socket", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    const serverCall = vi
      .fn()
      .mockReturnValueOnce(pending)
      .mockResolvedValue({ ok: true });
    const getUrl = vi.fn();
    const client = {
      serverCall,
      getUrl,
      defaultEnvironmentId: () => "env",
    } as unknown as RPCClient;
    const opening = terminalSession.open(client, "old", 40, 6);
    terminalSession.close();
    resolve({ terminal_id: "late", terminal_token: "token" });
    await opening;
    expect(terminalSession.phase).toBe("idle");
    expect(terminalSession.state).toBeNull();
    expect(getUrl).not.toHaveBeenCalled();
    expect(serverCall).toHaveBeenLastCalledWith(
      "terminal.close",
      { terminal_id: "late" },
      "old",
      { environmentId: "env" },
    );
  });

  it("ignores failure of an old creation after opening another session", async () => {
    let reject!: (error: Error) => void;
    const pending = new Promise((_r, r) => {
      reject = r;
    });
    const serverCall = vi
      .fn()
      .mockReturnValueOnce(pending)
      .mockReturnValue(new Promise(() => {}));
    const client = {
      serverCall,
      defaultEnvironmentId: () => "env",
    } as unknown as RPCClient;
    const old = terminalSession.open(client, "old", 40, 6);
    void terminalSession.open(client, "new", 90, 20);
    reject(new Error("old error"));
    await old;
    expect(terminalSession.belongsTo(client, "new")).toBe(true);
    expect(terminalSession.phase).toBe("connecting");
    expect(terminalSession.error).toBeNull();
    expect(terminalSession.state?.cols()).toBe(90);
  });

  it("can close during the socket handshake without an unhandled ws error", async () => {
    const http = createServer();
    const sockets = new Set<import("node:net").Socket>();
    http.on("connection", (socket) => {
      sockets.add(socket);
      socket.on("close", () => sockets.delete(socket));
    });
    await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
    const address = http.address() as import("node:net").AddressInfo;
    const serverCall = vi
      .fn()
      .mockResolvedValue({ terminal_id: "t", terminal_token: "token" });
    const client = {
      serverCall,
      defaultEnvironmentId: () => null,
      getUrl: () => `ws://127.0.0.1:${address.port}/ws`,
      getToken: () => undefined,
    } as unknown as RPCClient;
    try {
      await terminalSession.open(client, "session", 40, 6);
      terminalSession.close();
      await settle();
      expect(terminalSession.phase).toBe("idle");
      expect(serverCall).toHaveBeenLastCalledWith(
        "terminal.close",
        { terminal_id: "t" },
        "session",
        undefined,
      );
    } finally {
      for (const socket of sockets) socket.destroy();
      await new Promise<void>((resolve) => http.close(() => resolve()));
    }
  });

  it("releases the created terminal if its socket is rejected", async () => {
    const http = createServer((_request, response) => {
      response.writeHead(401);
      response.end();
    });
    await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
    const address = http.address() as import("node:net").AddressInfo;
    const serverCall = vi
      .fn()
      .mockResolvedValue({ terminal_id: "t", terminal_token: "token" });
    const client = {
      serverCall,
      defaultEnvironmentId: () => null,
      getUrl: () => `ws://127.0.0.1:${address.port}/ws`,
      getToken: () => undefined,
    } as unknown as RPCClient;
    try {
      await terminalSession.open(client, "session", 40, 6);
      await settle();
      expect(terminalSession.phase).toBe("error");
      expect(terminalSession.error).toContain("401");
      expect(serverCall).toHaveBeenCalledTimes(2);
      expect(serverCall).toHaveBeenLastCalledWith(
        "terminal.close",
        { terminal_id: "t" },
        "session",
        undefined,
      );
    } finally {
      terminalSession.close();
      await new Promise<void>((resolve) => http.close(() => resolve()));
    }
  });

  it("creates, attaches, renders backlog output, and sends input", async () => {
    const received: Frame[] = [];
    server = new WebSocketServer({ port: 0 });
    const port = await new Promise<number>((resolve) => {
      server!.on("listening", () => {
        const addr = server!.address();
        resolve(typeof addr === "object" && addr ? addr.port : 0);
      });
    });

    server.on("connection", (socket: ServerSocket) => {
      socket.on("message", (raw) => {
        const frame = JSON.parse(String(raw)) as Frame;
        received.push(frame);
        if (frame.type === "attach") {
          // Backlog: the prompt was written before the consumer attached.
          socket.send(
            JSON.stringify({
              type: "output",
              terminal_id: frame.terminal_id,
              data: b64("user@host:~$ "),
            }),
          );
        }
        if (frame.type === "input") {
          const text = Buffer.from(String(frame.data), "base64").toString();
          socket.send(
            JSON.stringify({
              type: "output",
              terminal_id: "t1",
              data: b64(text), // echo
            }),
          );
        }
      });
    });

    const client = {
      serverCall: async (fn: string) => {
        expect(fn).toBe("terminal.create");
        return {
          terminal_id: "t1",
          terminal_token: "tok",
          token: "tok",
          path: "/ws/terminal",
          session_id: "sess-1",
        };
      },
      defaultEnvironmentId: () => null,
      getUrl: () => `ws://127.0.0.1:${port}/ws`,
      getToken: () => undefined,
    } as unknown as RPCClient;

    await terminalSession.open(client, "sess-1", 40, 6);
    terminalSession.resize(55, 9);
    await settle(120);

    expect(terminalSession.phase).toBe("running");
    expect(received[0]).toMatchObject({
      type: "attach",
      session_id: "sess-1",
      terminal_id: "t1",
      terminal_token: "tok",
    });
    expect(received[1]).toMatchObject({ type: "resize", cols: 55, rows: 9 });

    const state = terminalSession.state!;
    const lines = state.readLines(0, 2);
    expect(lines[0]).toContain("user@host:~$");

    terminalSession.sendInput("ls\r");
    await settle(120);
    expect(state.readLines(0, 2).join("\n")).toContain("ls");
    const socket = [...server.clients][0]!;
    socket.send(JSON.stringify({ type: "exit", code: 7 }));
    await settle();
    const before = received.length;
    terminalSession.sendInput("must not execute\r");
    await settle();
    expect(terminalSession.exitCode).toBe(7);
    expect(received.length).toBe(before);
  });
});
