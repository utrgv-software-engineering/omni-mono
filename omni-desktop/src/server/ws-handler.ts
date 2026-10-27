import { uuidv4 } from '@omniagents/client/util/uuid';
import type { WebSocket } from 'ws';

import type { IpcRendererEvents } from '@/shared/types';

type InvokeMessage = {
  type: 'invoke';
  id: number;
  channel: string;
  args: unknown[];
};

/**
 * Per-invoke context handed to ctx-aware handlers. Server-mode managers
 * receive this object in the Electron `event` slot.
 *
 * `ws` is the active WebSocket the invoke came in on.
 */
export type HandlerContext = {
  sessionId: string;
  ws: WebSocket | null;
  sendToWindow: <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;
};

/** Raw handler — receives wire args only (public {@link WsHandler.handle}). */
type Handler = (...args: unknown[]) => unknown | Promise<unknown>;
/** Ctx-aware handler — receives the {@link HandlerContext} as its first arg. */
type CtxHandler = (ctx: HandlerContext, ...args: unknown[]) => unknown | Promise<unknown>;

type PersistentSession = {
  sessionId: string;
  ws: WebSocket | null;
  handlers: Map<string, CtxHandler>;
  cleanup?: () => Promise<void>;
  sendToWindow: <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;
  expiry?: ReturnType<typeof setTimeout>;
};

type EventInterceptor = (channel: string, args: unknown[]) => void;
type ResultWrapper = (result: unknown, args: unknown[]) => unknown;

/** A newer socket took over this session id; the displaced side must not dial back. */
export const WS_CLOSE_SESSION_DISPLACED = 4409;

/**
 * WebSocket handler that bridges JSON-RPC-like messages to handler functions,
 * mirroring Electron's ipc.handle() / sendToWindow() pattern.
 *
 * Supports two layers of handlers:
 * - Global handlers: shared across all clients (store, util, config)
 * - Per-session handlers: created per session, survive WebSocket reconnections
 *
 * Sessions survive a bounded reconnect grace period. When a client reconnects
 * with the same session ID, it reattaches to its existing managers/containers.
 */
export class WsHandler {
  private globalHandlers = new Map<string, CtxHandler>();
  /** Active WebSocket → session ID mapping (for message routing) */
  private wsSessions = new Map<WebSocket, PersistentSession>();
  /** sessionId → persistent session (survives disconnections) */
  private persistentSessions = new Map<string, PersistentSession>();
  private eventInterceptors: EventInterceptor[] = [];
  private resultWrappers = new Map<string, ResultWrapper>();
  private cleanupJobs = new Set<Promise<void>>();

  constructor(private readonly reconnectGraceMs = 60_000) {}

  /**
   * Register a global handler for a channel (shared across all clients).
   * The handler receives wire args only; the per-invoke {@link HandlerContext}
   * is discarded.
   */
  handle(channel: string, handler: Handler): void {
    this.globalHandlers.set(channel, (_ctx, ...args) => handler(...args));
  }

  /**
   * Register a ctx-aware global handler. The handler receives the
   * {@link HandlerContext} as its first argument, then the wire args.
   * {@link ServerIpcAdapter} uses this so managers get the context in the
   * Electron `event` slot.
   */
  handleCtx(channel: string, handler: CtxHandler): void {
    this.globalHandlers.set(channel, handler);
  }

  /**
   * Remove a global handler for a channel.
   */
  removeHandler(channel: string): void {
    this.globalHandlers.delete(channel);
  }

  /**
   * Register an interceptor that runs before any event is sent (via sendTo or sendToAll).
   * Interceptors receive a structuredClone of the args, so mutations never affect
   * the caller's original objects (e.g. SandboxManager status used by readiness checks).
   */
  addEventInterceptor(interceptor: EventInterceptor): void {
    this.eventInterceptors.push(interceptor);
  }

  /**
   * Register a wrapper that transforms the result of a handler for a given channel.
   * Applies to both global and per-session handlers.
   */
  addResultWrapper(channel: string, wrapper: ResultWrapper): void {
    this.resultWrappers.set(channel, wrapper);
  }

  private runEventInterceptors(channel: string, args: unknown[]): void {
    for (const interceptor of this.eventInterceptors) {
      interceptor(channel, args);
    }
  }

  /**
   * Send an event to all connected clients.
   */
  sendToAll<T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]): void {
    const wireArgs = structuredClone(args);
    this.runEventInterceptors(channel, wireArgs);
    const message = JSON.stringify({ type: 'event', channel, args: wireArgs });
    for (const session of this.wsSessions.values()) {
      if (session.ws && session.ws.readyState === 1 /* WebSocket.OPEN */) {
        session.ws.send(message);
      }
    }
  }

  /**
   * Send an event to a specific client.
   */
  sendTo<T extends keyof IpcRendererEvents>(ws: WebSocket, channel: T, ...args: IpcRendererEvents[T]): void {
    const wireArgs = structuredClone(args);
    this.runEventInterceptors(channel, wireArgs);
    if (ws.readyState === 1 /* WebSocket.OPEN */) {
      ws.send(JSON.stringify({ type: 'event', channel, args: wireArgs }));
    }
  }

  /**
   * Register a new WebSocket client connection.
   *
   * If a sessionId is provided and a persistent session exists for it, the WebSocket
   * is reattached to the existing session (preserving managers/containers).
   * Otherwise a new session is created via the onConnect callback.
   */
  addClient(
    ws: WebSocket,
    onConnect?: (session: {
      handle: (channel: string, handler: CtxHandler) => void;
      sendToWindow: <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => void;
      setCleanup: (cleanup: () => Promise<void>) => void;
    }) => void,
    sessionId?: string
  ): void {
    const existingSession = sessionId ? this.persistentSessions.get(sessionId) : undefined;

    if (existingSession) {
      clearTimeout(existingSession.expiry);
      existingSession.expiry = undefined;
      // Reattach: displace the previous socket, then bind the new one.
      //
      // 4409, not 1000. A client that loses its socket on a normal close
      // reconnects at once — and with the same id, so it displaces the
      // newcomer, which reconnects and displaces it back, ten times a
      // second for as long as both stay open (a Flutter client hard-coded
      // one id per install and did exactly this). 44xx is the range every
      // client here treats as terminal: the displaced side stops, the
      // newest socket keeps the session. A socket that is already closed
      // — the common reconnect case — is unaffected.
      if (existingSession.ws) {
        this.wsSessions.delete(existingSession.ws);
        if (existingSession.ws.readyState === 1 /* OPEN */) {
          console.warn(`[ws-handler] Session ${existingSession.sessionId} displaced a live socket`);
        }
        existingSession.ws.close(WS_CLOSE_SESSION_DISPLACED, 'Session reattached by another client');
      }
      existingSession.ws = ws;
      this.wsSessions.set(ws, existingSession);
      console.log(`[ws-handler] Session ${existingSession.sessionId} reattached`);
    } else {
      // New session
      const id = sessionId ?? uuidv4();
      const session: PersistentSession = {
        sessionId: id,
        ws,
        handlers: new Map(),
        sendToWindow: <T extends keyof IpcRendererEvents>(channel: T, ...args: IpcRendererEvents[T]) => {
          // Always send to the session's current WS (follows reattachment)
          if (session.ws && session.ws.readyState === 1) {
            this.sendTo(session.ws, channel, ...args);
          }
        },
      };
      this.wsSessions.set(ws, session);
      this.persistentSessions.set(id, session);

      if (onConnect) {
        onConnect({
          handle: (channel, handler) => {
            session.handlers.set(channel, handler);
          },
          sendToWindow: session.sendToWindow,
          setCleanup: (cleanup) => {
            session.cleanup = cleanup;
          },
        });
      }
      console.log(`[ws-handler] New session ${id} created`);
    }

    ws.on('message', (raw) => {
      void this.handleMessage(ws, raw);
    });

    ws.on('close', () => {
      const session = this.wsSessions.get(ws);
      if (session) {
        // Preserve document resources briefly for a socket reconnect. A new
        // document has its own ID and must not retain these resources forever.
        if (session.ws === ws) {
          session.ws = null;
          session.expiry = setTimeout(() => {
            if (session.ws || this.persistentSessions.get(session.sessionId) !== session) {
              return;
            }
            this.persistentSessions.delete(session.sessionId);
            this.cleanupSession(session);
          }, this.reconnectGraceMs);
          session.expiry.unref();
        }
        this.wsSessions.delete(ws);
        console.log(`[ws-handler] Session ${session.sessionId} detached (WS closed)`);
      }
    });
  }

  private cleanupSession(session: PersistentSession): void {
    const job = Promise.resolve()
      .then(() => session.cleanup?.())
      .catch((error: unknown) => {
        console.error('Error cleaning up session:', error);
      });
    this.cleanupJobs.add(job);
    void job.then(() => this.cleanupJobs.delete(job));
  }

  /**
   * Clean up all persistent sessions. Called on server shutdown.
   */
  async cleanupAllSessions(): Promise<void> {
    const sessions = [...this.persistentSessions.values()];
    for (const session of sessions) {
      clearTimeout(session.expiry);
    }
    this.persistentSessions.clear();
    this.wsSessions.clear();
    for (const session of sessions) {
      this.cleanupSession(session);
    }
    await Promise.all(this.cleanupJobs);
  }

  private async handleMessage(ws: WebSocket, raw: unknown): Promise<void> {
    let msg: InvokeMessage;
    try {
      msg = JSON.parse(String(raw)) as InvokeMessage;
    } catch {
      return;
    }

    if (msg.type !== 'invoke' || typeof msg.id !== 'number' || typeof msg.channel !== 'string') {
      return;
    }

    // Check per-session handlers first, then fall back to global handlers
    const session = this.wsSessions.get(ws);
    // Replaced/closed sockets must never dispatch.
    if (!session || session.ws !== ws) {
      return;
    }
    const handler = session.handlers.get(msg.channel) ?? this.globalHandlers.get(msg.channel);

    if (!handler) {
      ws.send(JSON.stringify({ type: 'response', id: msg.id, error: `No handler for channel: ${msg.channel}` }));
      return;
    }

    const ctx: HandlerContext = {
      sessionId: session.sessionId,
      ws,
      sendToWindow: session.sendToWindow,
    };

    try {
      let result = await handler(ctx, ...(msg.args ?? []));
      const wrapper = this.resultWrappers.get(msg.channel);
      if (wrapper) {
        result = wrapper(structuredClone(result), msg.args ?? []);
      }
      if (ws.readyState === 1) {
        ws.send(JSON.stringify({ type: 'response', id: msg.id, result }));
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err);
      if (ws.readyState === 1) {
        ws.send(JSON.stringify({ type: 'response', id: msg.id, error }));
      }
    }
  }
}
