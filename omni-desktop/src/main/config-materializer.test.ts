import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { materializeAgentConfig } from '@/main/config-materializer';
import type { McpConfig, NetworkConfig } from '@/shared/types';

const NET: NetworkConfig = {
  enabled: true,
  presets: [],
  allowlist: ['api.openai.com'],
};

const mcp = (): McpConfig => ({
  mcpServers: {
    github: { type: 'http', url: 'https://mcp', headers: { Authorization: 'token ghp_xxx' }, env: { DEBUG: '1' } },
  },
});

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'matz-'));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

const read = (name: string) => JSON.parse(readFileSync(join(dir, name), 'utf-8'));

describe('materializeAgentConfig', () => {
  it('writes configs verbatim', () => {
    materializeAgentConfig({ configDir: dir, mcp: mcp(), network: NET });
    expect(read('mcp.json')).toEqual(mcp());
    expect(read('network.json')).toEqual(NET);
  });

  it('can relinquish mcp.json while continuing to materialize the network config', () => {
    materializeAgentConfig({ configDir: dir, mcp: mcp(), network: NET });
    const canonical = { mcpServers: { canonical: { type: 'stdio', command: 'owned' } } };
    writeFileSync(join(dir, 'mcp.json'), `${JSON.stringify(canonical)}\n`);

    materializeAgentConfig({ configDir: dir, mcp: mcp(), network: NET, writeMcp: false });

    expect(read('mcp.json')).toEqual(canonical);
    expect(read('network.json')).toEqual(NET);
  });
});
