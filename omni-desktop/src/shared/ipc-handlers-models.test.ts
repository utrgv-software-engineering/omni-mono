import { describe, expect, it, vi } from 'vitest';

import { registerSettingsConfigHandlers } from '@/shared/ipc-handlers';
import type { IIpcListener } from '@/shared/ipc-listener';
import type { ModelsConfig } from '@/shared/types';

function setup() {
  let config: ModelsConfig = {
    version: 3,
    default: 'company/a',
    voice_default: null,
    providers: {
      company: { type: 'azure', models: { a: { model: 'a' } } },
      personal: { type: 'openai', models: { b: { model: 'b' } } },
    },
  };
  const handlers = new Map<string, (...args: any[]) => any>();
  const after = vi.fn();
  const stopAndWrite = vi.fn(async (_event, write) => write());
  registerSettingsConfigHandlers(
    { handle: (name: string, fn: any) => handlers.set(name, fn) } as IIpcListener,
    () =>
      ({
        get: () => config,
        set: (_key: string, next: ModelsConfig) => {
          config = next;
        },
      }) as never,
    after,
    {},
    { writeModels: stopAndWrite }
  );
  return { read: () => config, set: handlers.get('settings:set-models-config')!, after, stopAndWrite };
}

describe('workspace provider changes', () => {
  it('requires confirmation before stopping work or saving another provider', async () => {
    const h = setup();
    await expect(h.set(null, { ...h.read(), default: 'personal/b' })).rejects.toThrow('Confirm');
    expect(h.read().default).toBe('company/a');
    expect(h.stopAndWrite).not.toHaveBeenCalled();
    await h.set(
      null,
      { ...h.read(), default: 'personal/b' },
      { expectedProvider: 'company', confirmProviderChange: true }
    );
    expect(h.read().default).toBe('personal/b');
    expect(h.read().provider_scope_version).toBe(1);
    expect(h.stopAndWrite).toHaveBeenCalledOnce();
  });
  it('rejects a stale window even if it previously confirmed a switch', async () => {
    const h = setup();
    const old = h.read();
    await h.set(null, { ...old, default: 'personal/b' }, { expectedProvider: 'company', confirmProviderChange: true });
    await expect(h.set(null, old, { expectedProvider: 'company', confirmProviderChange: true })).rejects.toThrow(
      'another window'
    );
    expect(h.read().default).toBe('personal/b');
  });
  it('rejects model credentials before stopping runs and does not disclose secrets', async () => {
    const h = setup();
    const next = structuredClone(h.read());
    Object.assign(next.providers.company!.models.a!, { api_key: 'do-not-disclose' });
    await expect(h.set(null, next)).rejects.toThrow('provider-only');
    expect(h.stopAndWrite).not.toHaveBeenCalled();
    expect(h.after).not.toHaveBeenCalled();
  });
  it('does not silently acknowledge old mixed-provider conversations on an ordinary settings save', async () => {
    const h = setup();
    await h.set(null, h.read());
    expect(h.read().provider_scope_version).toBeUndefined();
    await h.set(null, h.read(), { expectedProvider: 'company', confirmProviderChange: true });
    expect(h.read().provider_scope_version).toBe(1);
  });
});
