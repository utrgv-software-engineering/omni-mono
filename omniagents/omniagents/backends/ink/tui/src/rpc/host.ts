import type { RPCClient } from "@omniagents/client/rpc/client";

/** The environment this runtime's agent host announced at the handshake:
 * where the file browser, git and mentions read when no session names one. */
export function defaultEnvironmentId(client: RPCClient): string | null {
  return client.initializeResult?.agent_host.default_environment_id ?? null;
}
