import { afterEach, describe, expect, it } from "vitest";
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
    await settle(120);

    expect(terminalSession.phase).toBe("running");
    expect(received[0]).toMatchObject({
      type: "attach",
      session_id: "sess-1",
      terminal_id: "t1",
      terminal_token: "tok",
    });
    expect(received[1]).toMatchObject({ type: "resize", cols: 40, rows: 6 });

    const state = terminalSession.state!;
    const lines = state.readLines(0, 2);
    expect(lines[0]).toContain("user@host:~$");

    terminalSession.sendInput("ls\r");
    await settle(120);
    expect(state.readLines(0, 2).join("\n")).toContain("ls");
  });
});
