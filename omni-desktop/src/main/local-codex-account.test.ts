import { describe, expect, it, vi } from 'vitest';

import { LocalCodexAccount, type LocalCodexAccountRuntime } from '@/main/local-codex-account';

const canonicalStatus = (options?: {
  state?: 'signed_in' | 'signed_out' | 'error';
  accountId?: string;
  durable?: boolean;
  scope?: 'host' | 'process' | null;
}): Record<string, unknown> => ({
  providers: [
    {
      id: 'openai-chatgpt',
      state: options?.state ?? 'signed_out',
      identity: options?.accountId ? { account_id: options.accountId } : null,
    },
  ],
  mutation_persistence: {
    codex_oauth: {
      durable: options?.durable ?? true,
      scope: options?.scope === undefined ? 'host' : options.scope,
    },
    pending_logins: { durable: false, scope: 'process' },
  },
});

const setup = (runtimeStatus?: Record<string, unknown>) => {
  const runtime: LocalCodexAccountRuntime = {
    status: vi.fn(async () => runtimeStatus ?? canonicalStatus()),
    mutate: vi.fn(),
  };
  return { runtime };
};

describe('LocalCodexAccount', () => {
  it('reports the runtime account status', async () => {
    const ctx = setup(canonicalStatus({ state: 'signed_in', accountId: 'acct-1' }));
    await expect(new LocalCodexAccount(ctx).status()).resolves.toEqual({ signedIn: true, accountId: 'acct-1' });
  });

  it('runs device-code start and completion polling through the broker', async () => {
    const ctx = setup();
    vi.mocked(ctx.runtime.status)
      .mockResolvedValueOnce(canonicalStatus())
      .mockResolvedValue(canonicalStatus({ state: 'signed_in', accountId: 'acct-rpc' }));
    vi.mocked(ctx.runtime.mutate)
      .mockResolvedValueOnce({
        state: 'pending',
        login_id: 'login-1',
        user_code: 'ABCD-EFGH',
        verification_url: 'https://auth.example/device',
        interval: 1,
      })
      .mockResolvedValueOnce({ state: 'pending' })
      .mockResolvedValueOnce({ state: 'completed' });
    const sleep = vi.fn(async () => {});
    const account = new LocalCodexAccount({ ...ctx, sleep });
    const onCode = vi.fn();

    await expect(account.link(onCode)).resolves.toEqual({ signedIn: true, accountId: 'acct-rpc' });
    expect(onCode).toHaveBeenCalledWith({
      userCode: 'ABCD-EFGH',
      verificationUri: 'https://auth.example/device',
    });
    expect(ctx.runtime.mutate).toHaveBeenNthCalledWith(1, {
      method: 'account_login_start',
      params: { provider: 'openai-chatgpt', mode: 'device_code' },
    });
    expect(ctx.runtime.mutate).toHaveBeenNthCalledWith(2, {
      method: 'account_login_complete',
      params: { login_id: 'login-1' },
    });
  });

  it('logs out through the broker', async () => {
    const ctx = setup(canonicalStatus({ state: 'signed_in' }));
    await new LocalCodexAccount(ctx).logout();
    expect(ctx.runtime.mutate).toHaveBeenCalledWith({
      method: 'account_logout',
      params: { provider: 'openai-chatgpt' },
    });
  });

  it('fails closed without a durable host-scoped attestation', async () => {
    const ctx = setup(canonicalStatus({ durable: false }));
    const account = new LocalCodexAccount(ctx);
    await expect(account.status()).rejects.toThrow('durable host-scoped');
    await expect(account.logout()).rejects.toThrow('durable host-scoped');
    await expect(account.link(vi.fn())).rejects.toThrow('durable host-scoped');
    expect(ctx.runtime.mutate).not.toHaveBeenCalled();
  });
});
