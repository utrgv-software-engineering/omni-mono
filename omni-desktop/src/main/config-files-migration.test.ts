import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { migrateAgentConfigFromFiles, removeRetiredProjectsMcpEntry } from '@/main/config-files-migration';
import type { SettingsConfigStore } from '@/shared/ipc-handlers';
import type { StoreData } from '@/shared/types';

/** Minimal in-memory store satisfying SettingsConfigStore. */
function fakeStore(): SettingsConfigStore & { data: Partial<StoreData> } {
  const data: Partial<StoreData> = {};
  return {
    data,
    get: (k) => data[k],
    set: (k, v) => {
      (data as Record<string, unknown>)[k] = v;
    },
  };
}

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'mig-'));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('migrateAgentConfigFromFiles', () => {
  it('imports on-disk files, dropping the retired omni-projects entry, and sets the guard', () => {
    writeFileSync(
      join(dir, 'models.json'),
      JSON.stringify({
        version: 3,
        default: null,
        voice_default: null,
        providers: { openai: { type: 'openai', models: {} } },
      })
    );
    writeFileSync(
      join(dir, 'mcp.json'),
      JSON.stringify({
        mcpServers: { mine: { type: 'stdio', command: 'x' }, 'omni-projects': { type: 'stdio', command: 'managed' } },
      })
    );
    writeFileSync(join(dir, '.env'), 'FOO=bar\n');

    const store = fakeStore();
    expect(migrateAgentConfigFromFiles(store, dir)).toBe(true);

    expect(store.data.modelsConfig?.providers.openai).toBeDefined();
    expect(store.data.mcpConfig?.mcpServers.mine).toBeDefined();
    expect(store.data.mcpConfig?.mcpServers['omni-projects']).toBeUndefined();
    expect(store.data.envVars).toBe('FOO=bar\n');
    expect(store.data.agentConfigMigratedFromFiles).toBe(true);
  });

  it('is idempotent — a second run imports nothing', () => {
    writeFileSync(join(dir, '.env'), 'A=1');
    const store = fakeStore();
    migrateAgentConfigFromFiles(store, dir);
    store.data.envVars = 'changed-by-user';
    expect(migrateAgentConfigFromFiles(store, dir)).toBe(false);
    expect(store.data.envVars).toBe('changed-by-user'); // not clobbered
  });

  it('returns false (but still sets the guard) when no files exist', () => {
    const store = fakeStore();
    expect(migrateAgentConfigFromFiles(store, dir)).toBe(false);
    expect(store.data.agentConfigMigratedFromFiles).toBe(true);
  });
});

describe('removeRetiredProjectsMcpEntry', () => {
  const readMcp = () => JSON.parse(readFileSync(join(dir, 'mcp.json'), 'utf-8')) as { mcpServers: object };

  it('drops the launcher-managed entry and keeps everything else', () => {
    writeFileSync(
      join(dir, 'mcp.json'),
      JSON.stringify({
        mcpServers: {
          mine: { type: 'stdio', command: 'x' },
          'omni-projects': { type: 'stdio', command: 'node', args: ['cli.js'], _managed: 'omni-launcher' },
        },
      })
    );
    removeRetiredProjectsMcpEntry(dir);
    expect(readMcp().mcpServers).toEqual({ mine: { type: 'stdio', command: 'x' } });
  });

  it('leaves a user-authored entry of the same name alone', () => {
    const config = { mcpServers: { 'omni-projects': { type: 'stdio', command: 'mine' } } };
    writeFileSync(join(dir, 'mcp.json'), JSON.stringify(config));
    removeRetiredProjectsMcpEntry(dir);
    expect(readMcp()).toEqual(config);
  });

  it('tolerates a missing mcp.json', () => {
    expect(() => removeRetiredProjectsMcpEntry(dir)).not.toThrow();
  });
});
