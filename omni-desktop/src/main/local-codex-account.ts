/**
 * Local-Electron ChatGPT account adapter over the Omniagents account RPCs. The
 * runtime owns the Codex OAuth store (durable at host scope); this class only
 * maps the desktop's `codex:*` IPC onto `account_*` management calls.
 */
import type { RpcMethodMap } from '@omniagents/protocol/gui-v2';

import type { ManagementAdminRequest } from '@/shared/management-admin';
import type { CodexAuthStatus, CodexDeviceCode } from '@/shared/types';

const CHATGPT_PROVIDER_ID = 'openai-chatgpt';
const DEFAULT_LOGIN_TIMEOUT_MS = 15 * 60_000;

export const durableLocalCodexAgentHostEnv = (env: Record<string, string>): Record<string, string> => ({
  ...env,
  // Always override a user-authored value. This is a trusted topology claim,
  // not a configurable runtime preference.
  OMNIAGENTS_CODEX_STORE_DURABILITY: 'host',
});

type AccountStatusResult = RpcMethodMap['account_status']['result'];

export type LocalCodexAccountRuntime = {
  status(): Promise<AccountStatusResult>;
  mutate(request: ManagementAdminRequest): Promise<unknown>;
};

type CanonicalAccount = CodexAuthStatus & {
  durableHostMutation: boolean;
};

const record = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;

/** Parse only the redacted fields the `codex:*` IPC needs. */
export const parseCanonicalCodexAccount = (value: unknown): CanonicalAccount | undefined => {
  const root = record(value);
  const providers = root?.['providers'];
  if (!Array.isArray(providers)) {
    return undefined;
  }
  const provider = providers.map(record).find((candidate) => candidate?.['id'] === CHATGPT_PROVIDER_ID);
  if (!provider) {
    return undefined;
  }

  const state = provider['state'];
  if (state !== 'signed_in' && state !== 'signed_out' && state !== 'error') {
    return undefined;
  }
  const identity = record(provider['identity']);
  const accountId = typeof identity?.['account_id'] === 'string' ? identity['account_id'] : undefined;
  const persistence = record(root?.['mutation_persistence']);
  const codexPersistence = record(persistence?.['codex_oauth']);
  const durableHostMutation = codexPersistence?.['durable'] === true && codexPersistence['scope'] === 'host';

  return {
    signedIn: state === 'signed_in' || state === 'error',
    ...(accountId ? { accountId } : {}),
    ...(state === 'error' ? { broken: true } : {}),
    durableHostMutation,
  };
};

/** Runtime half of the durable-mutation gate. ProcessManager combines this
 * attestation with its trusted Electron-only topology flag. */
export const hasDurableHostCodexMutation = (value: unknown): boolean =>
  parseCanonicalCodexAccount(value)?.durableHostMutation === true;

/** Only Codex OAuth credentials go through this path. API-key accounts and
 * provider selection require a separate durability contract. */
export const isDurableCodexAccountRequest = (request: ManagementAdminRequest): boolean => {
  switch (request.method) {
    case 'account_login_start':
      return (
        request.params.provider === CHATGPT_PROVIDER_ID &&
        (request.params.mode === 'device_code' || request.params.mode === 'browser')
      );
    case 'account_login_complete':
    case 'account_login_cancel':
      return true;
    case 'account_logout':
    case 'account_refresh':
      return request.params.provider === CHATGPT_PROVIDER_ID;
    case 'account_select':
      return false;
    default:
      return false;
  }
};

type LocalCodexAccountOptions = {
  runtime: LocalCodexAccountRuntime;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  loginTimeoutMs?: number;
};

export class LocalCodexAccount {
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;
  private readonly loginTimeoutMs: number;

  constructor(private readonly options: LocalCodexAccountOptions) {
    this.sleep =
      options.sleep ??
      ((ms) =>
        new Promise<void>((resolve) => {
          setTimeout(resolve, ms);
        }));
    this.now = options.now ?? Date.now;
    this.loginTimeoutMs = options.loginTimeoutMs ?? DEFAULT_LOGIN_TIMEOUT_MS;
  }

  /** Canonical state, only when the runtime reports durable host-scoped mutations. */
  private async requireDurableAccount(): Promise<CanonicalAccount> {
    const canonical = parseCanonicalCodexAccount(await this.options.runtime.status());
    if (!canonical) {
      throw new Error('Omniagents returned an invalid ChatGPT account status');
    }
    if (!canonical.durableHostMutation) {
      throw new Error('Omniagents does not report durable host-scoped ChatGPT account mutations');
    }
    return canonical;
  }

  async status(): Promise<CodexAuthStatus> {
    const { durableHostMutation: _durableHostMutation, ...status } = await this.requireDurableAccount();
    return status;
  }

  async link(onCode: (code: CodexDeviceCode) => void): Promise<CodexAuthStatus> {
    await this.requireDurableAccount();
    const started = record(
      await this.options.runtime.mutate({
        method: 'account_login_start',
        params: { provider: CHATGPT_PROVIDER_ID, mode: 'device_code' },
      })
    );
    if (!started) {
      throw new Error('Omniagents returned an invalid ChatGPT login response');
    }
    if (started['state'] === 'completed') {
      return this.status();
    }
    const loginId = typeof started['login_id'] === 'string' ? started['login_id'] : undefined;
    const userCode = typeof started['user_code'] === 'string' ? started['user_code'] : undefined;
    const verificationUri = typeof started['verification_url'] === 'string' ? started['verification_url'] : undefined;
    if (!loginId || !userCode || !verificationUri) {
      throw new Error('Omniagents returned an incomplete ChatGPT device-code response');
    }
    onCode({ userCode, verificationUri });

    const intervalMs = Math.max(Number(started['interval'] ?? 5), 1) * 1000;
    const deadline = this.now() + this.loginTimeoutMs;
    while (this.now() < deadline) {
      await this.sleep(intervalMs);
      const completed = record(
        await this.options.runtime.mutate({
          method: 'account_login_complete',
          params: { login_id: loginId },
        })
      );
      if (completed?.['state'] === 'completed') {
        return this.status();
      }
      if (completed?.['state'] !== 'pending') {
        throw new Error('Omniagents returned an invalid ChatGPT login completion response');
      }
    }
    await this.options.runtime
      .mutate({ method: 'account_login_cancel', params: { login_id: loginId } })
      .catch(() => undefined);
    throw new Error('ChatGPT device authorization timed out');
  }

  async logout(): Promise<void> {
    await this.requireDurableAccount();
    await this.options.runtime.mutate({
      method: 'account_logout',
      params: { provider: CHATGPT_PROVIDER_ID },
    });
  }
}
