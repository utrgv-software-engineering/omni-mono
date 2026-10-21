/**
 * Materialize the agent's on-disk config from the store.
 *
 * MCP servers and network policy are store-backed, but `omni serve` reads them
 * as files from its config dir (resolved via `XDG_CONFIG_HOME`). This module
 * bridges the two. Models are not materialized here: local-model-config.ts
 * reads and writes the shared `models.json` directly.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { McpConfig, NetworkConfig } from '@/shared/types';

function writeJson(path: string, data: unknown): void {
  // 0600 — the files can hold real secrets (MCP headers/env), so keep them
  // owner-only.
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, { encoding: 'utf-8', mode: 0o600 });
}

/**
 * Write `mcp.json` and `network.json` into `configDir`. `.env` is
 * deliberately NOT written here — the callers write it next to these files.
 */
export function materializeAgentConfig(opts: {
  configDir: string;
  mcp: McpConfig;
  network: NetworkConfig;
  /** Local Electron relinquishes mcp.json after canonical ownership transfer.
   * The network config remains Desktop-owned and continues to materialize. */
  writeMcp?: boolean;
}): void {
  const { configDir, mcp, network, writeMcp = true } = opts;
  mkdirSync(configDir, { recursive: true });
  if (writeMcp) {
    writeJson(join(configDir, 'mcp.json'), mcp);
  }
  writeJson(join(configDir, 'network.json'), network);
}
