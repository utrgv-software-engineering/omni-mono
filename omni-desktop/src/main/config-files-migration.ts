/**
 * One-time import of the agent's on-disk config files into the store.
 *
 * Before v23 the Settings UI wrote `models.json` / `mcp.json` / `network.json`
 * / `.env` directly to the config dir. Those are now store-backed and the
 * files are a derived artifact. This migration lifts any
 * existing files into the store once, then `config-materializer.ts` owns the
 * files going forward.
 *
 * Idempotent (guarded by `agentConfigMigratedFromFiles`).
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { SettingsConfigStore } from '@/shared/ipc-handlers';
import type { McpConfig, ModelsConfig, NetworkConfig } from '@/shared/types';

function readJson(path: string): unknown | null {
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as unknown;
  } catch {
    return null;
  }
}

function readText(path: string): string | null {
  try {
    return readFileSync(path, 'utf-8');
  } catch {
    return null;
  }
}

/** The retired launcher-managed MCP server (the old projects/tickets tools). */
const RETIRED_PROJECTS_MCP = 'omni-projects';

/**
 * Drop the retired launcher-managed `omni-projects` entry from the on-disk
 * `mcp.json`. Its CLI no longer ships, so leaving it would fail every session's
 * MCP startup. A user-authored entry of the same name (no `_managed` marker)
 * is left alone.
 */
export function removeRetiredProjectsMcpEntry(configDir: string): void {
  const path = join(configDir, 'mcp.json');
  const mcp = readJson(path) as { mcpServers?: Record<string, { _managed?: string }> } | null;
  if (mcp?.mcpServers?.[RETIRED_PROJECTS_MCP]?._managed !== 'omni-launcher') {
    return;
  }
  delete mcp.mcpServers[RETIRED_PROJECTS_MCP];
  writeFileSync(path, `${JSON.stringify(mcp, null, 2)}\n`, { encoding: 'utf-8', mode: 0o600 });
}

/**
 * Import on-disk config files into `store` (once). Returns true if anything was
 * imported. The retired managed `omni-projects` entry is never imported.
 */
export function migrateAgentConfigFromFiles(store: SettingsConfigStore, configDir: string): boolean {
  if (store.get('agentConfigMigratedFromFiles')) {
    return false;
  }
  let imported = false;

  const models = readJson(join(configDir, 'models.json'));
  if (models && typeof models === 'object' && 'version' in models) {
    store.set('modelsConfig', models as ModelsConfig);
    imported = true;
  }

  const mcp = readJson(join(configDir, 'mcp.json'));
  if (mcp && typeof mcp === 'object' && 'mcpServers' in mcp) {
    const servers = { ...(mcp as McpConfig).mcpServers };
    delete servers[RETIRED_PROJECTS_MCP];
    store.set('mcpConfig', { mcpServers: servers });
    imported = true;
  }

  const network = readJson(join(configDir, 'network.json'));
  if (network && typeof network === 'object') {
    store.set('networkConfig', network as NetworkConfig);
    imported = true;
  }

  const env = readText(join(configDir, '.env'));
  if (env !== null) {
    store.set('envVars', env);
    imported = true;
  }

  store.set('agentConfigMigratedFromFiles', true);
  return imported;
}
