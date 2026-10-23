/**
 * The TUI's connection to the agent runtime: the shared OmniAgents GUI
 * client (@omniagents/client), the same one the desktop and web UI use.
 */

import { useEffect, useState } from "react";
import { RPCClient } from "@omniagents/client/rpc/client";
import { getSessionRegistry } from "@omniagents/client/session/session-registry";
import { useAppStore } from "../store/appStore.js";

export interface UseRPCOptions {
  url: string;
  token?: string;
}

export interface UseRPCResult {
  client: RPCClient | null;
  connected: boolean;
  /** Connected and through the protocol handshake. */
  rpcReady: boolean;
  connecting: boolean;
  error: Error | null;
}

export function useRPC(options: UseRPCOptions): UseRPCResult {
  const [client, setClient] = useState<RPCClient | null>(null);
  const [state, setState] = useState<{
    connected: boolean;
    connecting: boolean;
    error: Error | null;
  }>({ connected: false, connecting: true, error: null });

  useEffect(() => {
    const rpcClient = new RPCClient(options.url, options.token);
    // The connection owns its conversations: they live and die with it.
    const releaseSessions = getSessionRegistry(rpcClient).retain();
    setClient(rpcClient);
    setState({ connected: false, connecting: true, error: null });

    const off = rpcClient.onConnectionState((connection) => {
      const connected = connection === "connected";
      if (connected) {
        // Components feature-detect protocol-gated panels from the grants
        // and route environment-scoped operations through the agent host.
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
      }
      setState((previous) => ({
        connected,
        connecting:
          connection === "connecting" || connection === "reconnecting",
        error: connected ? null : previous.error,
      }));
    });

    rpcClient.connect().catch((error: unknown) => {
      setState({
        connected: false,
        connecting: false,
        error: error instanceof Error ? error : new Error(String(error)),
      });
    });

    return () => {
      off();
      releaseSessions();
      rpcClient.dispose();
    };
  }, [options.url, options.token]);

  return {
    client,
    connected: state.connected,
    rpcReady: state.connected && !!client?.initializeResult,
    connecting: state.connecting,
    error: state.error,
  };
}
