/**
 * Pure builders for the `omni-projects` entry in an agent's
 * `~/.config/omni_code/mcp.json`. No Electron / fs imports, so both the
 * Electron and server config writers share one definition of the entry shape:
 * a stdio entry that spawns the bundled `cli.js`, which opens the local SQLite
 * `projects.db`.
 */

/** Channel name; also the `_managed` marker value the desktop writer uses. */
export const MCP_ENTRY_NAME = 'omni-projects';
export const MCP_MANAGED_MARKER = 'omni-launcher';

export interface StdioMcpEntry {
  type: 'stdio';
  command: string;
  args: string[];
  _managed?: string;
}

/** The managed entry the launcher injects into mcp.json. */
export type ManagedMcpEntry = StdioMcpEntry;

/**
 * Merge the launcher-managed `omni-projects` entry into a set of user MCP
 * servers. Idempotent and respectful of a user override: if an `omni-projects`
 * entry exists WITHOUT the managed marker the user has "claimed" the name, so
 * it is left untouched; otherwise the managed entry is (re)written. Pure — the
 * config materializer calls this before writing `mcp.json`.
 */
export function mergeManagedMcpEntry<T>(
  servers: Record<string, T>,
  managed: ManagedMcpEntry
): Record<string, T | ManagedMcpEntry> {
  const existing = servers[MCP_ENTRY_NAME] as { _managed?: string } | undefined;
  if (existing && existing._managed !== MCP_MANAGED_MARKER) {
    return servers;
  }
  return { ...servers, [MCP_ENTRY_NAME]: managed };
}

/** Spawn the bundled stdio MCP cli over the local DB. */
export function buildStdioMcpEntry(binPath: string): StdioMcpEntry {
  return { type: 'stdio', command: 'node', args: [binPath], _managed: MCP_MANAGED_MARKER };
}
