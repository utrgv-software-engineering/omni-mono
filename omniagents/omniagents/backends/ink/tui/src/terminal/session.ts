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

  private generation = 0;
  private owner: {
    client: RPCClient;
    sessionId: string;
    environmentId: string | null;
  } | null = null;
  private releaseTerminal: (() => void) | null = null;

  belongsTo(client: RPCClient, sessionId: string): boolean {
    return (
      this.owner?.client === client &&
      this.owner.sessionId === sessionId &&
      this.owner.environmentId === client.defaultEnvironmentId()
    );
  }

  private releaseOwnedTerminal(): void {
    const release = this.releaseTerminal;
    this.releaseTerminal = null;
    release?.();
  }

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
    if (
      this.belongsTo(client, sessionId) &&
      (this.phase === "connecting" || this.phase === "running")
    ) {
      this.resize(cols, rows);
      return;
    }
    this.close();
    const generation = this.generation;
    const current = () => this.generation === generation;
    const environmentId = client.defaultEnvironmentId();
    this.owner = { client, sessionId, environmentId };
    this.state = new XtermTerminalState(cols, rows);
    this.stateUnsub = this.state.onUpdate(() => this.bump());
    this.phase = "connecting";
    this.exitCode = null;
    this.error = null;
    this.outputBytes = 0;
    this.framesReceived = 0;
    this.bump();

    try {
      // Environment-scoped like every git_*/fs_* call — a fresh session
      // has no thread binding yet, so the routed environment must be
      // named explicitly.
      const created = (await client.serverCall(
        "terminal.create",
        { cols, rows },
        sessionId,
        environmentId ? { environmentId } : undefined,
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

      const release = () => {
        void client
          .serverCall(
            "terminal.close",
            { terminal_id: terminalId },
            created.session_id ?? sessionId,
            environmentId ? { environmentId } : undefined,
          )
          .catch(() => {});
      };
      if (!current()) {
        release();
        return;
      }
      this.releaseTerminal = release;

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
          if (!current()) return;
          this.releaseOwnedTerminal();
          socket.terminate();
          this.error = `terminal socket rejected: HTTP ${res?.statusCode ?? "?"}`;
          debugLog(`[term] ${this.error}`);
          this.phase = "error";
          this.bump();
        },
      );

      socket.on("open", () => {
        if (!current()) return;
        debugLog("[term] socket open, sending attach+resize");
        socket.send(
          JSON.stringify({
            type: "attach",
            session_id: created?.session_id ?? sessionId,
            terminal_id: terminalId,
            terminal_token: token,
          }),
        );
        socket.send(
          JSON.stringify({
            type: "resize",
            cols: this.state!.cols(),
            rows: this.state!.rows(),
          }),
        );
        this.phase = "running";
        this.bump();
      });

      socket.on("message", (raw: WebSocket.RawData) => {
        if (!current()) return;
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
        } else if (frame.type === "exit") {
          debugLog(`[term] exit frame code=${frame.code}`);
          this.exitCode = typeof frame.code === "number" ? frame.code : null;
          this.phase = "exited";
          this.bump();
        }
      });

      socket.on("close", (code: number, reason: Buffer) => {
        if (!current()) return;
        this.releaseOwnedTerminal();
        debugLog(`[term] socket close code=${code} reason=${String(reason)}`);
        if (this.phase === "running" || this.phase === "connecting") {
          this.error = code === 1000 ? null : `socket closed (${code})`;
          this.phase = "exited";
          this.bump();
        }
      });

      socket.on("error", (err: Error) => {
        if (!current()) return;
        this.releaseOwnedTerminal();
        debugLog(`[term] socket error: ${err.message}`);
        // unexpected-response already classified HTTP rejects.
        if (this.phase !== "error") {
          this.error = err.message;
          this.phase = "error";
          this.bump();
        }
      });
    } catch (e) {
      if (!current()) return;
      this.releaseOwnedTerminal();
      this.error = (e as Error).message ?? String(e);
      this.phase = "error";
      this.bump();
    }
  }

  sendInput(bytes: string): void {
    if (this.phase !== "running" || this.socket?.readyState !== WebSocket.OPEN)
      return;
    this.socket.send(
      JSON.stringify({
        type: "input",
        data: Buffer.from(bytes, "utf8").toString("base64"),
      }),
    );
  }

  resize(cols: number, rows: number): void {
    this.state?.resize(cols, rows);
    if (
      this.phase === "running" &&
      this.socket?.readyState === WebSocket.OPEN
    ) {
      this.socket.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  }

  close(): void {
    this.generation++;
    this.owner = null;
    if (this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify({ type: "close" }));
      } catch {
        // closing anyway
      }
    } else {
      this.releaseOwnedTerminal();
    }
    this.releaseTerminal = null;
    this.disposeSocket();
    this.state?.dispose();
    this.state = null;
    this.phase = "idle";
    this.bump();
  }

  private disposeSocket(): void {
    this.stateUnsub?.();
    this.stateUnsub = null;
    if (this.socket) {
      this.socket.removeAllListeners();
      // ws emits an asynchronous error when closed during its handshake.
      this.socket.on("error", () => {});
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
