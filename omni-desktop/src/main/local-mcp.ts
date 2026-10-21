/**
 * Local-Electron MCP management helpers. The Omniagents runtime owns MCP
 * configuration; the desktop only attests the host topology and gates
 * mutations on the runtime reporting durable host-scoped stores.
 */

const record = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;

const durableHostStore = (value: unknown): boolean => {
  const entry = record(value);
  return entry?.['durable'] === true && entry['scope'] === 'host';
};

/** Runtime half of the durable-mutation gate. ProcessManager combines this
 * with an Electron-only topology flag before exposing any MCP mutation. */
export const hasDurableHostMcpMutation = (value: unknown): boolean => {
  const root = record(value);
  const persistence = record(root?.['mutation_persistence']);
  return durableHostStore(persistence?.['user_config']) && durableHostStore(persistence?.['oauth_tokens']);
};

export const durableLocalMcpAgentHostEnv = (env: Record<string, string>): Record<string, string> => ({
  ...env,
  // These are trusted topology assertions, never user preferences.
  OMNIAGENTS_MCP_STORE_DURABILITY: 'host',
});

export const isMcpManagementMethod = (method: string): boolean => method.startsWith('mcp_');
