import type { RPCClientActor } from '@omniagents/client/machines/rpc-client.machine';
import { RPCClient } from '@omniagents/client/rpc/client';
import { getSessionRegistry } from '@omniagents/client/session/session-registry';
import { useSelector } from '@xstate/react';
import { createContext, type ReactNode, useContext, useEffect, useMemo } from 'react';

import { useUiConfig } from './ui-config';

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

export type RPCClientFactory = (url: string, token?: string) => RPCClient;

const defaultRPCClientFactory: RPCClientFactory = (url, token) => new RPCClient(url, token);

type SharedConnection = {
  client: RPCClient;
  owners: number;
  generation: number;
};

// Provider wrappers are view boundaries, not connection owners. The endpoint
// AND authentication identity form the pool key; never pool across credentials.
// Keys stay in memory and must not be logged or persisted.
const connectionPools = new WeakMap<RPCClientFactory, Map<string, SharedConnection>>();
function sharedConnection(factory: RPCClientFactory, url: string, token?: string) {
  let pool = connectionPools.get(factory);
  if (!pool) {
    pool = new Map();
    connectionPools.set(factory, pool);
  }
  const key = JSON.stringify([url, token ?? null]);
  let entry = pool.get(key);
  if (!entry) {
    const client = factory(url, token);
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
  createClient = defaultRPCClientFactory,
}: {
  children: ReactNode;
  createClient?: RPCClientFactory;
}) => {
  const { wsBaseUrl, token } = useUiConfig();

  const connection = useMemo(() => sharedConnection(createClient, wsBaseUrl, token), [createClient, token, wsBaseUrl]);
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
