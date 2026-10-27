import { useUiConfig } from '@omniagents/chat-ui/ui-config';
import type { RPCClientActor } from '@omniagents/client/machines/rpc-client.machine';
import type { Identity } from '@omniagents/client/protocol/gui-v2';
import { RPCClient } from '@omniagents/client/rpc/client';
import { getSessionRegistry } from '@omniagents/client/session/session-registry';
import { useSelector } from '@xstate/react';
import { createContext, type ReactNode, useContext, useEffect, useMemo } from 'react';

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

type RPCClientContextValue = {
  client: RPCClient;
  actor: RPCClientActor;
};

const RPCClientContext = createContext<RPCClientContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export type RPCClientFactory = (url: string, identity: Identity, token?: string) => RPCClient;

const defaultRPCClientFactory: RPCClientFactory = (url, identity, token) => new RPCClient(url, identity, token);

type SharedConnection = {
  client: RPCClient;
  owners: number;
  generation: number;
};

// Provider wrappers are view boundaries, not connection owners. The endpoint
// AND authentication identity form the pool key; never pool across credentials.
// Keys stay in memory and must not be logged or persisted.
const connectionPools = new WeakMap<RPCClientFactory, Map<string, SharedConnection>>();
function sharedConnection(factory: RPCClientFactory, url: string, identity: Identity, token?: string) {
  let pool = connectionPools.get(factory);
  if (!pool) {
    pool = new Map();
    connectionPools.set(factory, pool);
  }
  const key = JSON.stringify([url, token ?? null, identity.name, identity.version]);
  let entry = pool.get(key);
  if (!entry) {
    const client = factory(url, identity, token);
    entry = { client, owners: 0, generation: 0 };
    pool.set(key, entry);
  }
  const connection = entry;
  return {
    client: connection.client,
    retain() {
      connection.owners++;
      connection.generation++;
      const releaseSessions = getSessionRegistry(connection.client).retain();
      // The provider owns the connection, so it dials: anything under it may
      // use the client, not only a chat column booting. A boot's own
      // connect() joins this dial; a failed one is retried by the client.
      void connection.client.connect().catch(() => {});
      return () => {
        releaseSessions();
        connection.owners--;
        const generation = ++connection.generation;
        queueMicrotask(() => {
          if (connection.owners || generation !== connection.generation) {
            return;
          }
          // This pool entry is permanently retired. Merely disconnecting
          // lets a late async consumer reconnect an ownerless client.
          connection.client.dispose();
          if (pool.get(key) === connection) {
            pool.delete(key);
          }
        });
      };
    },
  };
}

export const RPCClientProvider = ({
  children,
  identity,
  createClient = defaultRPCClientFactory,
}: {
  children: ReactNode;
  /** The application connecting, as servers should know it. */
  identity: Identity;
  createClient?: RPCClientFactory;
}) => {
  const { wsBaseUrl, token } = useUiConfig();

  const connection = useMemo(
    () => sharedConnection(createClient, wsBaseUrl, identity, token),
    [createClient, token, wsBaseUrl, identity]
  );
  const { client } = connection;
  useEffect(() => connection.retain(), [connection]);

  const value = useMemo<RPCClientContextValue>(() => ({ client, actor: client.actor }), [client]);

  return <RPCClientContext.Provider value={value}>{children}</RPCClientContext.Provider>;
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/** Access the RPCClient instance for sending RPC calls. */
export const useRPCClient = (): RPCClient => {
  const ctx = useContext(RPCClientContext);
  if (!ctx) {
    throw new Error('RPCClientProvider is missing');
  }
  return ctx.client;
};

/** Reactive boolean — true only when the WebSocket is in the `connected` state. */
export const useRPCConnected = (): boolean => {
  const ctx = useContext(RPCClientContext);
  if (!ctx) {
    throw new Error('RPCClientProvider is missing');
  }
  return useSelector(ctx.actor, (snap) => snap.value === 'connected');
};
