/**
 * Runtime terminals: a shell the agent host owns, reached over its terminal
 * WebSocket (omniagents rpc/protocol.md, "Terminal WebSocket").
 *
 * A terminal is created with the `terminal.create` server function, then
 * driven over its own socket. Connection auth belongs to whoever dials the
 * socket (a browser exchanges its bearer token for a one-time ticket, a Node
 * process sends the Authorization header); the terminal's own credentials go
 * in the first frame, an `attach`, never in the URL. Closing the socket closes
 * the terminal on the server.
 */
import { classifyCloseCode } from '../lifecycle';
import type { ExecutionTarget } from './execution';

export type ServerCall = (
  func: string,
  args?: Record<string, unknown>,
  sessionId?: string,
  target?: ExecutionTarget
) => Promise<unknown>;

/** What the runtime hands back for one new terminal. */
export type TerminalAttach = {
  sessionId: string;
  terminalId: string;
  terminalToken: string;
  /** Socket path on the runtime's host, normally `/ws/terminal`. */
  path: string;
  /** The shell's working directory, when the runtime reports it. */
  cwd?: string;
};

/** Ask the runtime for a terminal in `sessionId`'s environment. */
export async function createRuntimeTerminal(
  serverCall: ServerCall,
  options: { sessionId: string; target?: ExecutionTarget; cols: number; rows: number }
): Promise<TerminalAttach> {
  // No `cwd`: the materialized environment's terminal defaults own it.
  const created = await serverCall(
    'terminal.create',
    { cols: options.cols, rows: options.rows },
    options.sessionId,
    options.target
  );
  if (typeof created !== 'object' || created === null) {
    throw new Error('terminal.create returned an unexpected payload');
  }
  const result = created as Record<string, unknown>;
  const terminalId = String(result.terminal_id ?? '').trim();
  const terminalToken = String(result.terminal_token ?? result.token ?? '').trim();
  if (!terminalId || !terminalToken) {
    throw new Error('The runtime did not return a usable terminal');
  }
  return {
    sessionId: String(result.session_id ?? options.sessionId).trim(),
    terminalId,
    terminalToken,
    path: String(result.path ?? '/ws/terminal').trim() || '/ws/terminal',
    cwd: typeof result.cwd === 'string' && result.cwd ? result.cwd : undefined,
  };
}

/** The terminal socket URL on the same host as a runtime's RPC socket, for a
 *  client dialing the runtime directly (a proxied page adds its own prefix). */
export function terminalSocketUrl(rpcSocketUrl: string, path: string): string {
  const url = new URL(rpcSocketUrl);
  url.pathname = path;
  url.search = '';
  return url.toString();
}

/** The part of the WHATWG WebSocket API a terminal stream uses (`ws` has it too). */
export type TerminalSocket = {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  addEventListener(type: 'open', listener: () => void): void;
  addEventListener(type: 'message', listener: (event: { data: unknown }) => void): void;
  addEventListener(type: 'close', listener: (event: { code: number }) => void): void;
  addEventListener(type: 'error', listener: (event: unknown) => void): void;
};

const OPEN = 1;

export type TerminalStreamHandlers = {
  /** Decoded shell output, in order. */
  onOutput(text: string): void;
  /** The shell exited, or the socket closed under it. Fires once. */
  onExit(code: number): void;
  /** The socket opened and the attach frame went out. */
  onOpen?(): void;
};

/** One attached terminal: input and resizes out, output and exit in. */
export class TerminalStream {
  private readonly decoder = new TextDecoder('utf-8', { fatal: false });
  private readonly encoder = new TextEncoder();
  private ended = false;

  constructor(
    private readonly socket: TerminalSocket,
    attach: TerminalAttach,
    private readonly handlers: TerminalStreamHandlers
  ) {
    socket.addEventListener('open', () => {
      // First on the wire: input and resizes are dropped until the socket opens.
      socket.send(
        JSON.stringify({
          type: 'attach',
          session_id: attach.sessionId,
          terminal_id: attach.terminalId,
          terminal_token: attach.terminalToken,
        })
      );
      handlers.onOpen?.();
    });
    socket.addEventListener('message', (event) => this.receive(event.data));
    socket.addEventListener('close', (event) => {
      // A clean shell exit arrives as an `exit` frame first; a close that
      // reaches here is transport-level. Deterministic rejections (44xx,
      // 1002/1003/1008) report a failed exit rather than a clean one.
      this.end(classifyCloseCode(event.code) === 'permanent' ? 1 : 0);
    });
  }

  write(data: string): void {
    this.send({ type: 'input', data: toBase64(this.encoder.encode(data)) });
  }

  resize(cols: number, rows: number): void {
    this.send({ type: 'resize', cols, rows });
  }

  /** Close the terminal. No exit is reported for a close the caller asked for. */
  close(): void {
    this.send({ type: 'close' });
    this.ended = true;
    try {
      this.socket.close();
    } catch {}
  }

  private send(frame: Record<string, unknown>): void {
    if (this.ended || this.socket.readyState !== OPEN) {
      return;
    }
    try {
      this.socket.send(JSON.stringify(frame));
    } catch {}
  }

  private receive(raw: unknown): void {
    let message: unknown;
    try {
      message = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (typeof message !== 'object' || message === null) {
      return;
    }
    const frame = message as Record<string, unknown>;
    if (frame.type === 'output' && typeof frame.data === 'string' && frame.data) {
      // One decoder per stream: a character split across frames still decodes.
      this.handlers.onOutput(this.decoder.decode(fromBase64(frame.data), { stream: true }));
    } else if (frame.type === 'exit') {
      this.end(typeof frame.code === 'number' ? frame.code : 0);
      try {
        this.socket.close();
      } catch {}
    }
  }

  private end(code: number): void {
    if (this.ended) {
      return;
    }
    this.ended = true;
    this.handlers.onExit(code);
  }
}

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}
