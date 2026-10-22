import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { removeRetiredState } from '@/main/legacy-cleanup';

const git = (cwd: string, ...args: string[]) =>
  execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=T', '-C', cwd, ...args], { stdio: 'pipe' });

const makeRemote = (root: string): string => {
  const seed = path.join(root, 'seed');
  mkdirSync(seed);
  git(seed, 'init', '-b', 'main');
  writeFileSync(path.join(seed, 'README.md'), 'hi\n');
  git(seed, 'add', '.');
  git(seed, 'commit', '-m', 'init');
  const bare = path.join(root, 'remote.git');
  execFileSync('git', ['clone', '--bare', seed, bare], { stdio: 'pipe' });
  return bare;
};

const clone = (remote: string, dest: string) => {
  mkdirSync(path.dirname(dest), { recursive: true });
  execFileSync('git', ['clone', remote, dest], { stdio: 'pipe' });
};

describe('removeRetiredState', () => {
  it('removes retired dirs and clean per-chat clones, moving work aside and keeping user folders', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'omni-legacy-'));
    const config = path.join(root, 'config');
    const remote = makeRemote(root);
    mkdirSync(path.join(config, 'sandbox', 'omniagents', 'traces'), { recursive: true });
    mkdirSync(path.join(config, 'snapshots'), { recursive: true });
    writeFileSync(path.join(config, 'snapshots', '.old.tar.tmp'), 'x');

    const chat = path.join(config, 'workspaces', 'workspace_a');
    clone(remote, path.join(chat, 'clean'));
    clone(remote, path.join(chat, 'edited'));
    writeFileSync(path.join(chat, 'edited', 'README.md'), 'changed\n');
    clone(remote, path.join(chat, 'committed'));
    writeFileSync(path.join(chat, 'committed', 'new.txt'), 'new\n');
    git(path.join(chat, 'committed'), 'add', '.');
    git(path.join(chat, 'committed'), 'commit', '-m', 'local only');
    const userRepo = path.join(root, 'user-repo');
    clone(remote, userRepo);
    symlinkSync(userRepo, path.join(chat, 'linked'));
    mkdirSync(path.join(chat, '.omniagents'));

    await removeRetiredState(config);

    expect(existsSync(path.join(config, 'sandbox'))).toBe(false);
    expect(existsSync(path.join(config, 'snapshots'))).toBe(false);
    expect(existsSync(path.join(chat, 'clean'))).toBe(false);
    const retired = path.join(config, 'retired-clones', 'workspace_a');
    expect(existsSync(path.join(chat, 'edited'))).toBe(false);
    expect(readFileSync(path.join(retired, 'edited', 'README.md'), 'utf8')).toBe('changed\n');
    expect(existsSync(path.join(chat, 'committed'))).toBe(false);
    expect(existsSync(path.join(retired, 'committed', 'new.txt'))).toBe(true);
    expect(existsSync(path.join(userRepo, 'README.md'))).toBe(true);
    expect(existsSync(path.join(chat, 'linked'))).toBe(true);
    expect(existsSync(path.join(chat, '.omniagents'))).toBe(true);

    // Idempotent.
    await expect(removeRetiredState(config)).resolves.toBeUndefined();
  });
});
