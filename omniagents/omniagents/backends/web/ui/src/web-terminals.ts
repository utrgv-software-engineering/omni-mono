import { createTerminalStore, type TerminalStore } from '@omniagents/chat-ui/terminal/terminal-store';
import type { UiConfig } from '@omniagents/chat-ui/ui-config';
import type { RPCClient } from '@omniagents/client/rpc/client';
import { createRuntimeTerminal, TerminalStream } from '@omniagents/client/rpc/terminal';
import { withConnectTicket } from '@omniagents/client/rpc/ws-ticket';

type Listeners = {
  output(scope: string, id: string, data: string): void;
  exited(scope: string, id: string, code: number): void;
};

/**
 * Terminals for the web UI, scoped by chat session. The page holds each
 * shell's socket, so a reload closes its terminals.
 */
export function createWebTerminals(client: RPCClient, config: UiConfig): TerminalStore {
  const streams = new Map<string, { scope: string; stream: TerminalStream }>();
  const listeners = new Set<Listeners>();

  return createTerminalStore({
    list: async (scope) => [...streams].filter(([, entry]) => entry.scope === scope).map(([id]) => id),
    create: async (sessionId) => {
      await client.serverCall('session.ensure', { session_id: sessionId }, sessionId);
      const attach = await createRuntimeTerminal(
        (func, args, session, target) => client.serverCall(func, args, session, target),
        {
          sessionId,
          cols: 80,
          rows: 24,
        }
      );
      // The bearer token buys a one-time ticket; credentials never ride the URL.
      const url = await withConnectTicket(`${config.wsOrigin}${config.resolvePath(attach.path)}`, config.token);
      const id = attach.terminalId;
      const stream = new TerminalStream(new WebSocket(url), attach, {
        onOutput: (text) => listeners.forEach((listener) => listener.output(sessionId, id, text)),
        onExit: (code) => {
          streams.delete(id);
          listeners.forEach((listener) => listener.exited(sessionId, id, code));
        },
      });
      streams.set(id, { scope: sessionId, stream });
      return id;
    },
    dispose: async (id) => {
      streams.get(id)?.stream.close();
      streams.delete(id);
    },
    write: (id, data) => streams.get(id)?.stream.write(data),
    resize: (id, cols, rows) => streams.get(id)?.stream.resize(cols, rows),
    subscribe: (handlers) => {
      listeners.add(handlers);
      return () => listeners.delete(handlers);
    },
  });
}
