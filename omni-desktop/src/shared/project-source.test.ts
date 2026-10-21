/**
 * Tests for project-source.ts — type guards and extraction helpers.
 */
import { describe, expect, it } from 'vitest';

import {
  findDuplicateSourceIdentity,
  normalizeGitRemoteUrl,
  normalizeLocalSourcePath,
  sourceIdentityKey,
  validateProjectSources,
} from '@/shared/project-source';
import type { ProjectSource } from '@/shared/types';

const localSource: ProjectSource = {
  id: 'local',
  mountName: 'my-app',
  kind: 'local',
  workspaceDir: '/home/user/projects/my-app',
};
const gitSource: ProjectSource = {
  id: 'git',
  mountName: 'repo',
  kind: 'git-remote',
  repoUrl: 'https://github.com/org/repo.git',
};

describe('source identity', () => {
  it('normalizes local paths by trimming separators and Windows casing', () => {
    expect(normalizeLocalSourcePath(' C:\\Users\\Me\\Repo\\ ')).toBe('c:/users/me/repo');
    expect(normalizeLocalSourcePath('/Users/Me/Repo///')).toBe('/Users/Me/Repo');
  });

  it('normalizes common Git URL spellings to the same identity', () => {
    expect(normalizeGitRemoteUrl('https://github.com/Owner/Repo.git/')).toBe('github.com/owner/repo');
    expect(normalizeGitRemoteUrl('git@github.com:owner/repo.git')).toBe('github.com/owner/repo');
    expect(
      sourceIdentityKey({
        id: 's1',
        mountName: 'repo',
        kind: 'git-remote',
        repoUrl: 'ssh://git@github.com/owner/repo.git',
      })
    ).toBe('git-remote:github.com/owner/repo');
  });

  it('detects duplicate local source identities with different mount names', () => {
    const duplicate = findDuplicateSourceIdentity([
      { id: 's1', mountName: 'repo', kind: 'local', workspaceDir: '/tmp/repo' },
      { id: 's2', mountName: 'repo-copy', kind: 'local', workspaceDir: '/tmp/repo/' },
    ]);
    expect(duplicate?.id).toBe('s2');
  });

  it('validates mount names and source identities independently', () => {
    expect(() =>
      validateProjectSources([
        { id: 's1', mountName: 'repo', kind: 'git-remote', repoUrl: 'https://github.com/owner/repo.git' },
        { id: 's2', mountName: 'repo-copy', kind: 'git-remote', repoUrl: 'git@github.com:owner/repo.git' },
      ])
    ).toThrow('already includes that repository');
    expect(() =>
      validateProjectSources([
        { id: 's1', mountName: 'repo', kind: 'local', workspaceDir: '/tmp/repo-a' },
        { id: 's2', mountName: 'repo', kind: 'local', workspaceDir: '/tmp/repo-b' },
      ])
    ).toThrow('Duplicate mount name');
  });
});
