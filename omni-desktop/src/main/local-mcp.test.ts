import { describe, expect, it } from 'vitest';

import { durableLocalMcpAgentHostEnv, hasDurableHostMcpMutation } from '@/main/local-mcp';

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

describe('local MCP runtime helpers', () => {
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
});
