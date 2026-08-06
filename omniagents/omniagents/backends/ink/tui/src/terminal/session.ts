// One docked terminal session: terminal.create over RPC, then the
// /ws/terminal socket speaking base64 PTY frames into the emulation
// seam. Module-level singleton (v1: one terminal per TUI); React reads
// it through useSyncExternalStore via subscribe/getVersion.

import WebSocket from "ws";
import type { RPCClient } from "../rpc/client.js";
import { debugLog } from "../utils/debugLog.js";
import { XtermTerminalState, type TerminalState } from "./state.js";

export type TerminalPhase =
  | "idle"
  | "connecting"
  | "running"
  | "exited"
  | "error";

type CreateResult = {
  terminal_id?: string;
  terminal_token?: string;
  token?: string;
  path?: string;
  session_id?: string;
};

class TerminalSession {
  state: TerminalState | null = null;
  phase: TerminalPhase = "idle";
  exitCode: number | null = null;
  error: string | null = null;
  /** Diagnostics: PTY output received since open. */
  outputBytes = 0;
  framesReceived = 0;

  private socket: WebSocket | null = null;
  private version = 0;
  private listeners = new Set<() => void>();
  private stateUnsub: (() => void) | null = null;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getVersion = (): number => this.version;

  private bump(): void {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  async open(
    client: RPCClient,
    sessionId: string,
    cols: number,
    rows: number,
  ): Promise<void> {
    if (this.phase === "connecting" || this.phase === "running") return;
    this.disposeSocket();
    this.state?.dispose();
    this.state = new XtermTerminalState(cols, rows);
    this.stateUnsub = this.state.onUpdate(() => this.bump());
    this.phase = "connecting";
    this.exitCode = null;
    this.error = null;
    this.outputBytes = 0;
    this.framesReceived = 0;
    this.bump();

    try {
      const created = (await client.serverCall(
        "terminal.create",
        { cols, rows },
        sessionId,
      )) as CreateResult;
      debugLog(
        `[term] created: keys=${Object.keys(created ?? {}).join(",")} id=${created?.terminal_id} path=${created?.path}`,
      );
      const terminalId = String(created?.terminal_id ?? "").trim();
      const token = String(
        created?.terminal_token ?? created?.token ?? "",
      ).trim();
      const path = String(created?.path ?? "/ws/terminal").trim();
      if (!terminalId || !token) {
        throw new Error("terminal.create returned no terminal");
      }

      // Same-origin as the main RPC socket; node's ws supports the
      // Bearer header directly (no browser ticket dance needed).
      const url = new URL(client.getUrl());
      url.pathname = path;
      url.search = "";
      const authToken = client.getToken();
      debugLog(`[term] connecting ${url.toString()} bearer=${!!authToken}`);
      const socket = new WebSocket(
        url.toString(),
        authToken
          ? { headers: { Authorization: `Bearer ${authToken}` } }
          : undefined,
      );
      this.socket = socket;

      // HTTP-level rejections (404 route, 401 auth) surface here, not
      // as normal close frames.
      socket.on(
        "unexpected-response",
        (_req: unknown, res: { statusCode?: number }) => {
          this.error = `terminal socket rejected: HTTP ${res?.statusCode ?? "?"}`;
          debugLog(`[term] ${this.error}`);
          this.phase = "error";
          this.bump();
        },
      );

      socket.on("open", () => {
        debugLog("[term] socket open, sending attach+resize");
        socket.send(
          JSON.stringify({
            type: "attach",
            session_id: created?.session_id ?? sessionId,
            terminal_id: terminalId,
            terminal_token: token,
          }),
        );
        socket.send(JSON.stringify({ type: "resize", cols, rows }));
        this.phase = "running";
        this.bump();
      });

      socket.on("message", (raw: WebSocket.RawData) => {
        let frame: { type?: string; data?: string; code?: number };
        try {
          frame = JSON.parse(String(raw));
        } catch {
          return;
        }
        this.framesReceived += 1;
        if (frame.type === "output" && typeof frame.data === "string") {
          const bytes = Buffer.from(frame.data, "base64");
          this.outputBytes += bytes.length;
          if (this.framesReceived <= 5) {
            debugLog(
              `[term] output frame ${bytes.length}B: ${JSON.stringify(bytes.toString("utf8").slice(0, 80))}`,
            );
          }
          this.state?.feed(bytes);
          if (this.framesReceived <= 5) {
            const st = this.state;
            if (st) {
              setTimeout(() => {
                const sample = st
                  .readLines(0, 3)
                  .map((l) => l.replace(/\x1b\[[0-9;]*m/g, ""))
                  .join("|");
                debugLog(
                  `[term] grid: lines=${st.lineCount()} base=${st.baseY()} cols=${st.cols()} rows=${st.rows()} head=${JSON.stringify(sample.slice(0, 100))}`,
                );
              }, 50);
            }
          }
        } else if (frame.type === "exit") {
          debugLog(`[term] exit frame code=${frame.code}`);
          this.exitCode = typeof frame.code === "number" ? frame.code : null;
          this.phase = "exited";
          this.bump();
        }
      });

      socket.on("close", (code: number, reason: Buffer) => {
        debugLog(`[term] socket close code=${code} reason=${String(reason)}`);
        if (this.phase === "running" || this.phase === "connecting") {
          this.error = code === 1000 ? null : `socket closed (${code})`;
          this.phase = "exited";
          this.bump();
        }
      });

      socket.on("error", (err: Error) => {
        debugLog(`[term] socket error: ${err.message}`);
        // unexpected-response already classified HTTP rejects.
        if (this.phase !== "error") {
          this.error = err.message;
          this.phase = "error";
          this.bump();
        }
      });
    } catch (e) {
      this.error = (e as Error).message ?? String(e);
      this.phase = "error";
      this.bump();
    }
  }

  sendInput(bytes: string): void {
    if (this.phase !== "running" || !this.socket) return;
    this.socket.send(
      JSON.stringify({
        type: "input",
        data: Buffer.from(bytes, "utf8").toString("base64"),
      }),
    );
  }

  resize(cols: number, rows: number): void {
    this.state?.resize(cols, rows);
    if (this.phase === "running" && this.socket) {
      this.socket.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  }

  close(): void {
    if (this.socket && this.phase === "running") {
      try {
        this.socket.send(JSON.stringify({ type: "close" }));
      } catch {
        // closing anyway
      }
    }
    this.disposeSocket();
    this.phase = "idle";
    this.bump();
  }

  private disposeSocket(): void {
    this.stateUnsub?.();
    this.stateUnsub = null;
    if (this.socket) {
      this.socket.removeAllListeners();
      try {
        this.socket.close();
      } catch {
        // already closed
      }
      this.socket = null;
    }
  }
}

export const terminalSession = new TerminalSession();
