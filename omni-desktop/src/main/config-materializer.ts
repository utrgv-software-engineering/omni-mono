/**
 * Materialize the agent's on-disk config from the store.
 *
 * MCP servers are store-backed, but `omni serve` reads them as a file from
 * its config dir (resolved via `XDG_CONFIG_HOME`). This module
 * bridges the two. Models are not materialized here: local-model-config.ts
 * reads and writes the shared `models.json` directly.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { McpConfig } from '@/shared/types';

function writeJson(path: string, data: unknown): void {
  // 0600 — the files can hold real secrets (MCP headers/env), so keep them
  // owner-only.
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, { encoding: 'utf-8', mode: 0o600 });
}

/**
 * Write `mcp.json` into `configDir`. `.env` is deliberately NOT written here —
 * the callers write it next to this file.
 */
export function materializeAgentConfig(opts: {
  configDir: string;
  mcp: McpConfig;
  /** Local Electron relinquishes mcp.json after canonical ownership transfer. */
  writeMcp?: boolean;
}): void {
  const { configDir, mcp, writeMcp = true } = opts;
  mkdirSync(configDir, { recursive: true });
  if (writeMcp) {
    writeJson(join(configDir, 'mcp.json'), mcp);
  }
}
