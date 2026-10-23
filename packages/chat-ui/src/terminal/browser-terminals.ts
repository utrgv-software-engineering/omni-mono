import type { RPCClient } from '@omniagents/client/rpc/client';
import { createRuntimeTerminal, TerminalStream } from '@omniagents/client/rpc/terminal';
import { withConnectTicket } from '@omniagents/client/rpc/ws-ticket';

import type { UiConfig } from '../ui-config';
import { createTerminalStore, type TerminalStore } from './terminal-store';

type Listeners = {
  output(scope: string, id: string, data: string): void;
  exited(scope: string, id: string, code: number): void;
};

/**
 * Terminals for a browser page (the omniagents web UI, Studio), scoped by
 * chat session: the page dials each shell's socket on the runtime itself,
 * so a reload closes its terminals. The desktop routes them through its
 * main process instead.
 */
export function createBrowserTerminals(client: RPCClient, config: UiConfig): TerminalStore {
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
