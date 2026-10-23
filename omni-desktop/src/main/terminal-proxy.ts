import { classifyCloseCode, DEFAULT_LIFECYCLE_POLICY } from '@omniagents/client/lifecycle';
import { createRuntimeTerminal, terminalSocketUrl, TerminalStream } from '@omniagents/client/rpc/terminal';
import { WebSocket } from 'ws';

import { wsAuthOptions } from '@/lib/ws-auth';
import { initializeMainRpcConnection } from '@/main/omniagents-rpc-handshake';
import type { ProcessManager } from '@/main/process-manager';
import type { IpcRendererEvents } from '@/shared/types';

/**
 * Bridges `terminal:*` IPC to `omni serve`'s WebSocket protocol so the
 * shell is owned by the agent (via `omniagents.rpc.terminal.TerminalManager`
 * backed by `SessionPtyBackend`), not as a child of the launcher.
 *
 * Two WebSockets per terminal:
 *   - One JSON-RPC connection (`/ws`) shared across all terminals on a
 *     given tab, used to call `session.ensure` and `terminal.create`.
 *   - One I/O socket (`/ws/terminal`) per terminal. Connection auth is the
 *     `Authorization: Bearer` upgrade header (when the server is
 *     authenticated); the `terminal_id`/`terminal_token` returned by
 *     `terminal.create` ride in the FIRST message — an `attach` frame —
 *     never in the dial URL (omniagents rpc/protocol.md, Terminal WebSocket).
 *
 * Closing the I/O socket triggers `close_terminal` on the omni serve side
 * automatically (see `omniagents/backends/server/app.py` finally block) —
 * no explicit `terminal.close` RPC needed.
 */

type WindowSender = <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;

export type ConsoleErrorKind =
  'process_not_ready' | 'rpc_failed' | 'session_unavailable' | 'terminal_unavailable' | 'backend_no_pty_stream';

export class ConsoleError extends Error {
  readonly kind: ConsoleErrorKind;
  /**
   * True when the failure is terminal per the shared lifecycle policy
   * (`@/shared/lifecycle`): the peer closed with a deterministic rejection
   * (44xx auth/feature codes, 1002/1003/1008) and redialing the same way
   * will fail identically.
   */
  readonly permanent: boolean;
  /** WebSocket close code when the failure came from a socket close. */
  readonly closeCode?: number;

  constructor(kind: ConsoleErrorKind, message: string, opts: { permanent?: boolean; closeCode?: number } = {}) {
    super(message);
    this.name = 'ConsoleError';
    this.kind = kind;
    this.permanent = opts.permanent ?? false;
    this.closeCode = opts.closeCode;
  }
}

type ProxiedTerminal = {
  id: string;
  tabId: string;
  stream: TerminalStream;
};

type RpcCall = {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

type TabRpc = {
  socket: WebSocket;
  ready: Promise<void>;
  pending: Map<number, RpcCall>;
  nextId: number;
  /** Session id created via `session.ensure` — shared by every terminal in this tab. */
  sessionId: string | null;
  /** Resolved after the first session.ensure completes. */
  sessionReady: Promise<string> | null;
};

/** Per-call deadline from the standard lifecycle policy (protocol.md §"Connection Lifecycle"). */
const RPC_TIMEOUT_MS = DEFAULT_LIFECYCLE_POLICY.rpcTimeoutMs;

export class TerminalProxy {
  private terminals = new Map<string, ProxiedTerminal>();
  private tabRpc = new Map<string, TabRpc>();

  constructor(
    private readonly deps: {
      processManager: ProcessManager;
      sendToWindow: WindowSender;
    }
  ) {}

  async create(tabId: string): Promise<string> {
    const status = this.deps.processManager.getStatus(tabId);
    if ((status.type !== 'running' && status.type !== 'connecting') || !('data' in status) || !status.data.wsUrl) {
      throw new ConsoleError('process_not_ready', 'Open a code session before launching a terminal.');
    }
    const wsUrl = status.data.wsUrl;
    const authToken = status.data.authToken;
    const environmentId = status.data.environmentId;
    const workspaceId = status.data.workspaceId;
    const environmentGeneration = status.data.environmentGeneration;
    if (!environmentId || !workspaceId || environmentGeneration === undefined) {
      throw new ConsoleError(
        'terminal_unavailable',
        'This code session does not have an execution environment for terminals.'
      );
    }

    const rpc = await this.ensureTabRpc(tabId, wsUrl, authToken);
    const sessionId = await this.ensureSession(rpc);

    let attach;
    try {
      attach = await createRuntimeTerminal(
        (func, args, session, target) =>
          this.rpcCall(rpc, 'server_call', {
            function: func,
            args,
            session_id: session,
            workspace_id: target?.workspaceId,
            environment_id: target?.environmentId,
            environment_generation: target?.environmentGeneration,
          }),
        {
          sessionId,
          target: { workspaceId, environmentId, environmentGeneration },
          cols: 80,
          rows: 24,
        }
      );
    } catch (err) {
      if (err instanceof ConsoleError) {
        throw err;
      }
      throw new ConsoleError('terminal_unavailable', (err as Error).message ?? String(err));
    }

    const ioSocket = new WebSocket(terminalSocketUrl(wsUrl, attach.path), wsAuthOptions(authToken));
    ioSocket.on('error', (err) => {
      console.error('[TerminalProxy] /ws/terminal error', err);
    });
    const id = attach.terminalId;
    const stream = new TerminalStream(ioSocket, attach, {
      onOutput: (text) => this.deps.sendToWindow('terminal:output', tabId, id, text),
      onExit: (code) => {
        this.terminals.delete(id);
        this.deps.sendToWindow('terminal:exited', tabId, id, code);
      },
    });
    this.terminals.set(id, { id, tabId, stream });
    return id;
  }

  write(id: string, data: string): void {
    this.terminals.get(id)?.stream.write(data);
  }

  resize(id: string, cols: number, rows: number): void {
    this.terminals.get(id)?.stream.resize(cols, rows);
  }

  async dispose(id: string): Promise<void> {
    const entry = this.terminals.get(id);
    if (!entry) {
      return;
    }
    this.terminals.delete(id);
    entry.stream.close();
  }

  async disposeAllForTab(tabId: string): Promise<void> {
    const ids = [...this.terminals.values()].filter((t) => t.tabId === tabId).map((t) => t.id);
    await Promise.allSettled(ids.map((id) => this.dispose(id)));
    const rpc = this.tabRpc.get(tabId);
    if (rpc) {
      this.tabRpc.delete(tabId);
      this.failPendingRpc(rpc, new ConsoleError('rpc_failed', 'tab disposed'));
      try {
        rpc.socket.close();
      } catch {
        // ignore
      }
    }
  }

  async disposeAll(): Promise<void> {
    const ids = [...this.terminals.keys()];
    await Promise.allSettled(ids.map((id) => this.dispose(id)));
    const tabIds = [...this.tabRpc.keys()];
    for (const tabId of tabIds) {
      const rpc = this.tabRpc.get(tabId);
      if (!rpc) {
        continue;
      }
      this.tabRpc.delete(tabId);
      this.failPendingRpc(rpc, new ConsoleError('rpc_failed', 'shutting down'));
      try {
        rpc.socket.close();
      } catch {
        // ignore
      }
    }
  }

  listIdsForTab(tabId: string): string[] {
    return [...this.terminals.values()].filter((t) => t.tabId === tabId).map((t) => t.id);
  }

  // ----------------------------- internals -----------------------------

  private async ensureTabRpc(tabId: string, wsUrl: string, authToken?: string): Promise<TabRpc> {
    const existing = this.tabRpc.get(tabId);
    if (existing) {
      await existing.ready;
      if (existing.socket.readyState === WebSocket.OPEN) {
        return existing;
      }
      this.tabRpc.delete(tabId);
    }

    const socket = new WebSocket(wsUrl, wsAuthOptions(authToken));
    let resolveOpen!: () => void;
    let rejectOpen!: (error: Error) => void;
    const opened = new Promise<void>((resolve, reject) => {
      resolveOpen = resolve;
      rejectOpen = reject;
    });
    const rpc: TabRpc = {
      socket,
      pending: new Map(),
      nextId: 1,
      sessionId: null,
      sessionReady: null,
      ready: Promise.resolve(),
    };

    socket.on('message', (raw) => this.handleRpcMessage(rpc, raw));
    socket.once('open', resolveOpen);
    socket.once('error', rejectOpen);
    socket.on('close', (code, reason) => {
      // Classify per the shared lifecycle table: 44xx / 1002 / 1003 / 1008
      // are deterministic rejections (e.g. omni serve refusing the token) —
      // mark the rejection permanent so callers don't treat it as a blip.
      // No pending call may remain unresolved after the socket closes.
      const permanent = classifyCloseCode(code) === 'permanent';
      const detail = reason.toString('utf-8');
      this.failPendingRpc(
        rpc,
        new ConsoleError('rpc_failed', `/ws connection closed (code ${code}${detail ? `: ${detail}` : ''})`, {
          permanent,
          closeCode: code,
        })
      );
      if (this.tabRpc.get(tabId) === rpc) {
        this.tabRpc.delete(tabId);
      }
    });
    socket.on('error', () => {
      // Errors are surfaced via the per-call rejection; the close handler
      // cleans up state.
    });

    // A transport-level open socket is not an initialized GUI RPC
    // connection. Complete the protocol handshake before any session or
    // terminal operation can use this channel.
    rpc.ready = opened.then(async () => {
      await initializeMainRpcConnection({
        name: 'omni-desktop-terminal-proxy',
        capabilities: { terminal: true },
        request: (_method, params) => this.rpcCall(rpc, 'initialize', params),
        notify: (_method, params) =>
          new Promise<void>((resolve, reject) => {
            socket.send(JSON.stringify({ jsonrpc: '2.0', method: 'initialized', params }), (error) => {
              if (error) {
                reject(error);
              } else {
                resolve();
              }
            });
          }),
      });
    });

    this.tabRpc.set(tabId, rpc);
    try {
      await rpc.ready;
    } catch (err) {
      this.tabRpc.delete(tabId);
      if (err instanceof ConsoleError) {
        throw err;
      }
      throw new ConsoleError('rpc_failed', `failed to open /ws to omni serve: ${(err as Error).message ?? err}`);
    }
    return rpc;
  }

  private async ensureSession(rpc: TabRpc): Promise<string> {
    if (rpc.sessionId) {
      return rpc.sessionId;
    }
    if (rpc.sessionReady) {
      return rpc.sessionReady;
    }
    rpc.sessionReady = (async () => {
      const res = await this.rpcCall(rpc, 'server_call', {
        function: 'session.ensure',
        args: {},
      });
      if (!isObject(res)) {
        throw new ConsoleError('session_unavailable', 'session.ensure returned no payload');
      }
      const sid = String(res['session_id'] ?? '').trim();
      if (!sid) {
        throw new ConsoleError('session_unavailable', 'session.ensure returned no session_id');
      }
      rpc.sessionId = sid;
      return sid;
    })();
    return rpc.sessionReady;
  }

  private rpcCall(rpc: TabRpc, method: string, params: object): Promise<unknown> {
    const id = rpc.nextId++;
    const payload = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    return new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        rpc.pending.delete(id);
        reject(new ConsoleError('rpc_failed', `${method} timed out`));
      }, RPC_TIMEOUT_MS);
      rpc.pending.set(id, { resolve, reject, timer });
      try {
        rpc.socket.send(payload);
      } catch (err) {
        clearTimeout(timer);
        rpc.pending.delete(id);
        reject(new ConsoleError('rpc_failed', `${method} send failed: ${(err as Error).message ?? err}`));
      }
    });
  }

  private handleRpcMessage(rpc: TabRpc, raw: unknown): void {
    let msg: unknown;
    try {
      msg = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (!isObject(msg)) {
      return;
    }
    const id = msg['id'];
    if (typeof id !== 'number') {
      return;
    }
    const pending = rpc.pending.get(id);
    if (!pending) {
      return;
    }
    rpc.pending.delete(id);
    clearTimeout(pending.timer);
    if ('error' in msg && isObject(msg['error'])) {
      const errMsg = String((msg['error'] as Record<string, unknown>)['message'] ?? 'rpc error');
      pending.reject(new ConsoleError('rpc_failed', errMsg));
      return;
    }
    pending.resolve(msg['result']);
  }

  private failPendingRpc(rpc: TabRpc, err: Error): void {
    for (const [, p] of rpc.pending) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    rpc.pending.clear();
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
