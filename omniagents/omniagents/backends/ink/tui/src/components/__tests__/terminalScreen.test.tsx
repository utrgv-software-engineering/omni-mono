/**
 * Renders the REAL TerminalScreen through Ink against a frame-accurate
 * /ws/terminal server — the full pipeline: create → attach → output
 * frames → emulator → useSyncExternalStore → Ink frame. Exists because
 * a live session showed frames feeding the grid while the screen
 * stayed blank; this pins where that pipeline breaks.
 */

import React from "react";
import { Writable } from "node:stream";
import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it } from "vitest";
import { render } from "ink";
import { WebSocketServer, type WebSocket as ServerSocket } from "ws";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { MouseProvider } from "../../contexts/MouseContext.js";
import { TerminalScreen, terminalHint } from "../TerminalScreen.js";
import { terminalSession } from "../../terminal/session.js";
import type { RPCClient } from "../../rpc/client.js";

const settle = (ms: number) =>
  new Promise((resolve) => setTimeout(resolve, ms));

function b64(text: string): string {
  return Buffer.from(text, "utf8").toString("base64");
}

class CaptureStream extends Writable {
  columns = 100;
  rows = 40;
  frames: string[] = [];
  override _write(chunk: Buffer, _enc: string, done: () => void): void {
    this.frames.push(String(chunk));
    done();
  }
  get output(): string {
    return this.frames.join("");
  }
}

class FakeStdin extends EventEmitter {
  isTTY = true;
  setRawMode() {}
  setEncoding() {}
  resume() {}
  pause() {}
  read() {
    return null;
  }
  unref() {}
  ref() {}
}

describe("TerminalScreen end-to-end render", () => {
  let server: WebSocketServer | null = null;
  let instance: { unmount: () => void } | null = null;

  afterEach(() => {
    instance?.unmount();
    instance = null;
    terminalSession.close();
    server?.close();
    server = null;
  });

  it("paints PTY output into the Ink frame", async () => {
    server = new WebSocketServer({ port: 0 });
    const port = await new Promise<number>((resolve) => {
      server!.on("listening", () => {
        const addr = server!.address();
        resolve(typeof addr === "object" && addr ? addr.port : 0);
      });
    });
    server.on("connection", (socket: ServerSocket) => {
      socket.on("message", (raw) => {
        const frame = JSON.parse(String(raw)) as { type?: string };
        if (frame.type === "attach") {
          // Mirror the live shell: OSC title, bracketed paste, then a
          // styled two-line prompt — the exact bytes from the field log.
          socket.send(
            JSON.stringify({
              type: "output",
              terminal_id: "t1",
              data: b64("\x1b]0;emm@omarchy:~/x\x07"),
            }),
          );
          socket.send(
            JSON.stringify({
              type: "output",
              terminal_id: "t1",
              data: b64("\x1b[?2004h"),
            }),
          );
          socket.send(
            JSON.stringify({
              type: "output",
              terminal_id: "t1",
              data: b64(
                "\r\n\x1b[1;36momniagents\x1b[0m \x1b[3;90m~/x\x1b[0m\r\n\x1b[1;35m❯\x1b[0m ",
              ),
            }),
          );
        }
      });
    });

    const client = {
      serverCall: async () => ({
        terminal_id: "t1",
        terminal_token: "tok",
        token: "tok",
        path: "/ws/terminal",
        session_id: "sess-1",
      }),
      defaultEnvironmentId: () => null,
      getUrl: () => `ws://127.0.0.1:${port}/ws`,
      getToken: () => undefined,
    } as unknown as RPCClient;

    const stdout = new CaptureStream();
    instance = render(
      <KeypressProvider>
        <MouseProvider>
          <TerminalScreen
            active={true}
            height={20}
            client={client}
            sessionId="sess-1"
            captured={true}
            onCapturedChange={() => {}}
            onExit={() => {}}
          />
        </MouseProvider>
      </KeypressProvider>,
      {
        stdout: stdout as unknown as NodeJS.WriteStream,
        stdin: new FakeStdin() as unknown as NodeJS.ReadStream,
        debug: false,
        exitOnCtrlC: false,
        patchConsole: false,
      },
    );

    await settle(400);

    const plain = stdout.output.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "");
    expect(terminalSession.phase).toBe("running");
    expect(terminalSession.outputBytes).toBeGreaterThan(0);
    // The prompt must be IN THE RENDERED FRAME, not just in the grid.
    expect(plain).toContain("omniagents");
    expect(plain).toContain("❯");
  });
});

describe("terminal footer hint", () => {
  const base = {
    phase: "running",
    captured: false,
    outputBytes: 10,
    exitCode: null,
    error: null,
  };

  it("mentions ctrl+] only when there is a keyboard to give back", () => {
    // It has one meaning: release. Naming it while the app already owns
    // the keyboard is what made it read as a navigation key.
    expect(terminalHint({ ...base, captured: true })).toContain(
      "ctrl+] release",
    );
    expect(terminalHint({ ...base, captured: false })).not.toContain("ctrl+]");
  });

  it("teaches one way out of each state, never two", () => {
    // Captured, that is ctrl+] and nothing else: alt+1..5 still works but
    // naming it offered a second exit from the state whose whole problem
    // was having more than one. Uncaptured, it is esc, as everywhere else.
    const typing = terminalHint({ ...base, captured: true });
    expect(typing).toContain("ctrl+] release");
    expect(typing).not.toContain("alt+1..5");
    expect(typing).not.toContain("esc");

    const free = terminalHint({ ...base, captured: false });
    expect(free).toContain("esc chat");
    expect(free).not.toContain("alt+1..5");
    expect(free).not.toContain("ctrl+]");
  });

  it("says release even before the shell has printed anything", () => {
    // The no-output branch used to be tested before `captured`, so a
    // shell that had not printed its prompt yet advertised the wrong key.
    expect(terminalHint({ ...base, captured: true, outputBytes: 0 })).toContain(
      "ctrl+] release",
    );
  });

  it("keeps the is-it-alive nudge for a shell nobody is typing into", () => {
    const hint = terminalHint({ ...base, captured: false, outputBytes: 0 });
    expect(hint).toContain("no output yet");
    expect(hint).not.toContain("ctrl+]");
  });

  it("reports how an exited shell ended", () => {
    expect(terminalHint({ ...base, phase: "exited", exitCode: 130 })).toContain(
      "exited 130 · enter restart",
    );
    expect(
      terminalHint({ ...base, phase: "exited", exitCode: 1, error: "boom" }),
    ).toContain("(boom)");
  });
});
