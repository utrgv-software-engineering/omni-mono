import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock('@/renderer/services/ipc', () => ({ emitter: { invoke: mocks.invoke } }));

import { managementAdminApi } from './management-admin';

describe('managementAdminApi', () => {
  beforeEach(() => mocks.invoke.mockReset());

  it('uses generated MCP mutation fields and strict result decoding', async () => {
    mocks.invoke.mockResolvedValue({ ok: true, server_name: 'github' });

    await expect(managementAdminApi.deleteMcpServer('github')).resolves.toBeUndefined();
    expect(mocks.invoke).toHaveBeenCalledWith('management-runtime:mutate', {
      method: 'mcp_delete_server',
      params: { server_name: 'github' },
    });
  });
});
