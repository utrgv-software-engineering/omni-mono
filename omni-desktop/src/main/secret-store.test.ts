/**
 * LocalSecretStore — AES roundtrip, cross-shell sharing (two instances on the
 * same dir, as desktop + local-server do), and back-compat reads of the legacy
 * Electron `{v, enc}` envelope.
 */
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LocalSecretStore } from '@/main/secret-store';

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'omni-secrets-'));
  delete process.env.OMNI_SECRET_KEY; // use a generated secret.key under `dir`
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('LocalSecretStore', () => {
  it('round-trips a git token (encrypted at rest, not plaintext on disk)', async () => {
    const store = new LocalSecretStore(dir);
    await store.setGitToken('github-oauth', 'ghp_secret123');

    expect(await store.getGitToken('github-oauth')).toBe('ghp_secret123');
    const raw = readFileSync(join(dir, 'git-secrets.json'), 'utf-8');
    expect(raw).not.toContain('ghp_secret123'); // AES, not plaintext
    expect(JSON.parse(raw).gitTokens['github-oauth']).toHaveProperty('iv');
  });

  it('shares the file across instances (desktop ↔ local-server)', async () => {
    await new LocalSecretStore(dir).setGitToken('c1', 'tok-1');
    // A second instance on the same dir (the other shell) reads it back.
    expect(await new LocalSecretStore(dir).getGitToken('c1')).toBe('tok-1');
  });

  it('delete removes a git token', async () => {
    const store = new LocalSecretStore(dir);
    await store.setGitToken('c', 'v');
    await store.deleteGitToken('c');
    expect(await store.getGitToken('c')).toBeUndefined();
  });

  it('round-trips oauth bundles in a separate slot', () => {
    const store = new LocalSecretStore(dir);
    store.setOauthTokens('entra', { access: 'a', refresh: 'r', expires: 123 });
    expect(store.getOauthTokens('entra')).toEqual({ access: 'a', refresh: 'r', expires: 123 });
    // git + oauth slots are independent.
    expect(store.getOauthTokens('nope')).toBeUndefined();
    store.deleteOauthTokens('entra');
    expect(store.getOauthTokens('entra')).toBeUndefined();
  });

  it('honors OMNI_SECRET_KEY when set', async () => {
    process.env.OMNI_SECRET_KEY = Buffer.alloc(32, 7).toString('base64');
    const store = new LocalSecretStore(dir);
    await store.setGitToken('k', 'val');
    expect(await store.getGitToken('k')).toBe('val');
  });
});
