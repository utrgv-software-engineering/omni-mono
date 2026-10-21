/**
 * React hook for managing RPC client connection and state
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { RPCClient } from "../rpc/client.js";
import { useAppStore } from "../store/appStore.js";

export interface UseRPCOptions {
  url: string;
  token?: string;
  debug?: boolean;
}

export interface UseRPCResult {
  client: RPCClient | null;
  connected: boolean;
  rpcReady: boolean;
  connecting: boolean;
  error: Error | null;
  sendMessage: (
    content: string,
    sessionId?: string,
  ) => Promise<{ run_id: string; session_id: string }>;
}

/**
 * Hook for managing RPC client lifecycle
 */
export function useRPC(options: UseRPCOptions): UseRPCResult {
  const [client, setClient] = useState<RPCClient | null>(null);
  const [connected, setConnected] = useState(false);
  const [rpcReady, setRpcReady] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const clientRef = useRef<RPCClient | null>(null);

  useEffect(() => {
    const rpcClient = new RPCClient({
      url: options.url,
      token: options.token,
      debug: options.debug,
    });

    clientRef.current = rpcClient;

    // Set up event listeners
    rpcClient.on("connected", () => {
      setConnected(true);
      setConnecting(false);
      setError(null);
    });

    rpcClient.on("rpc_ready", () => {
      setRpcReady(true);
      // Publish the negotiated handshake so components can feature-detect
      // protocol-gated panels (grants) and route environment-scoped
      // operations (agent_host). Re-runs on every reconnect handshake.
      const init = rpcClient.initializeResult;
      useAppStore.getState().actions.setServerInit(
        init
          ? {
              protocolVersion: init.protocol_version,
              grantedExperimental: [
                ...init.capabilities.experimental_operations,
              ],
              agentHost: {
                agentHostId: init.agent_host.agent_host_id,
                defaultWorkspaceId:
                  init.agent_host.default_workspace_id ?? null,
                defaultEnvironmentId:
                  init.agent_host.default_environment_id ?? null,
              },
            }
          : null,
      );
    });

    rpcClient.on("disconnected", () => {
      setConnected(false);
      setRpcReady(false);
    });

    rpcClient.on("error", (err: Error) => {
      setError(err);
      setConnecting(false);
    });

    rpcClient.on("reconnecting", () => {
      setConnecting(true);
    });

    rpcClient.on(
      "reconnect_failed",
      (info?: { permanent?: boolean; attempts?: number }) => {
        // Terminal failure: either a permanent rejection (auth, handshake)
        // or the retry budget ran out. Distinct from transient
        // "reconnecting" states, which keep `connecting` true.
        const attempts = info?.attempts ?? 0;
        const message =
          attempts > 1
            ? `Failed to reconnect to server (gave up after ${attempts} attempts)`
            : "Connection rejected by server (not retryable)";
        setError(new Error(message));
        setConnecting(false);
      },
    );

    // Connect
    rpcClient.connect().catch((err) => {
      setError(err);
      setConnecting(false);
    });

    setClient(rpcClient);

    // Cleanup
    return () => {
      rpcClient.disconnect();
      clientRef.current = null;
    };
  }, [options.url, options.token, options.debug]);

  const sendMessage = useCallback(
    async (
      content: string,
      sessionId?: string,
    ): Promise<{ run_id: string; session_id: string }> => {
      if (!clientRef.current) {
        throw new Error("RPC client not initialized");
      }
      return clientRef.current.startRun(
        content,
        clientRef.current.defaultEnvironmentSelection(),
        sessionId,
      );
    },
    [],
  );

  return {
    client,
    connected,
    rpcReady,
    connecting,
    error,
    sendMessage,
  };
}
