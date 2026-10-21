import { describe, expect, it, vi } from 'vitest';

import {
  durableLocalMcpAgentHostEnv,
  hasDurableHostMcpMutation,
  LocalMcpConfigOwner,
} from '@/main/local-mcp-config-owner';
import type { McpConfig } from '@/shared/types';

const durable = (servers: unknown[]) => ({
  servers,
  user_mcp_allowed: true,
  write_target: '/ignored/mcp.json',
  mutation_persistence: {
    user_config: { durable: true, scope: 'host' },
    oauth_tokens: { durable: true, scope: 'host' },
    pending_auth: { durable: false, scope: 'process' },
  },
});

const setup = (mcpConfig: McpConfig = { mcpServers: {} }) => {
  let ownership: 'omniagents' | undefined;
  const set = vi.fn((_key: 'mcpConfigOwnership', value: 'omniagents') => {
    ownership = value;
  });
  const owner = new LocalMcpConfigOwner({
    store: {
      get: ((key: 'mcpConfigOwnership' | 'mcpConfig') =>
        key === 'mcpConfigOwnership' ? ownership : mcpConfig) as never,
      set,
    },
    environment: () => ({ MCP_URL: 'https://expanded.test' }),
  });
  return { owner, set };
};

describe('local MCP ownership', () => {
  it('overrides a spoofed durability claim', () => {
    expect(
      durableLocalMcpAgentHostEnv({
        KEEP: 'yes',
        OMNIAGENTS_MCP_STORE_DURABILITY: 'process',
      })
    ).toEqual({ KEEP: 'yes', OMNIAGENTS_MCP_STORE_DURABILITY: 'host' });
  });

  it('requires both durable stores', () => {
    expect(hasDurableHostMcpMutation(durable([]))).toBe(true);
    expect(
      hasDurableHostMcpMutation({
        ...durable([]),
        mutation_persistence: {
          ...durable([]).mutation_persistence,
          oauth_tokens: { durable: false, scope: null },
        },
      })
    ).toBe(false);
  });

  it('marks ownership after exact redacted parity', () => {
    const { owner, set } = setup({
      mcpServers: {
        github: {
          type: 'http',
          url: '${MCP_URL}',
          headers: { Authorization: 'secret', 'X-Empty': '' },
        },
      },
    });
    owner.ensureOwnership(
      durable([
        {
          server_name: 'github',
          source: 'user',
          transport: 'http',
          params: {
            url: 'https://expanded.test',
            headers: { Authorization: { is_set: true }, 'X-Empty': { is_set: true } },
          },
          server_options: {},
        },
        { server_name: 'project-only', source: 'project', transport: 'stdio', params: {}, server_options: {} },
      ])
    );
    expect(set).toHaveBeenCalledWith('mcpConfigOwnership', 'omniagents');
    expect(owner.isOwned()).toBe(true);
  });

  it('does not compare or expose secret values', () => {
    const { owner } = setup({
      mcpServers: { github: { type: 'http', url: 'https://mcp', headers: { Authorization: 'one-secret' } } },
    });
    expect(() =>
      owner.ensureOwnership(
        durable([
          {
            server_name: 'github',
            source: 'user',
            transport: 'http',
            params: { url: 'https://mcp', headers: { Authorization: { is_set: true } } },
            server_options: {},
          },
        ])
      )
    ).not.toThrow();
  });

  it('refuses mismatch and missing durability', () => {
    const mismatch = setup({ mcpServers: { github: { type: 'http', url: 'https://expected' } } });
    expect(() => mismatch.owner.ensureOwnership(durable([]))).toThrow('disagree');
    expect(mismatch.set).not.toHaveBeenCalled();

    const unattested = setup();
    expect(() => unattested.owner.ensureOwnership({ ...durable([]), mutation_persistence: {} })).toThrow(
      'did not attest'
    );
  });

  it('still checks runtime durability after ownership but does not rerun parity', () => {
    const { owner, set } = setup();
    owner.ensureOwnership(durable([]));
    owner.ensureOwnership(durable([]));
    expect(set).toHaveBeenCalledTimes(1);
    expect(() => owner.ensureOwnership({ ...durable([]), mutation_persistence: {} })).toThrow('did not attest');
  });
});
