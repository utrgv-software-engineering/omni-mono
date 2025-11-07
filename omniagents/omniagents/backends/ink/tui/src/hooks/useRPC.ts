/**
 * React hook for managing RPC client connection and state
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { RPCClient } from "../rpc/client.js";

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

    rpcClient.on("reconnect_failed", () => {
      setError(new Error("Failed to reconnect to server"));
      setConnecting(false);
    });

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
      return clientRef.current.startRun(content, sessionId);
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
