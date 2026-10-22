import { DatabaseSync } from 'node:sqlite';

import { execFileSync } from 'child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const dirs = vi.hoisted(() => ({ root: '' }));

vi.mock('@/main/util', () => ({
  getDefaultWorkspaceDir: () => path.join(dirs.root, 'Workspace'),
  getProjectDir: (slug: string) => path.join(dirs.root, 'Workspace', 'Projects', slug),
  getOmniConfigDir: () => dirs.root,
  projectCheckoutPath: (projectId: string, sourceId: string) => path.join(dirs.root, 'checkouts', projectId, sourceId),
}));

import { ProjectManager, registerProjectHandlers } from '@/main/project-manager';
import { importLegacyProjectsDb } from '@/main/projects-import';
import type { Project, StoreData } from '@/shared/types';
import { StubIpc } from '@/test-helpers/stub-ipc';

/** Minimal electron-store stand-in over a plain object. */
const makeStore = (projects: Project[] = []) => {
  const data: Partial<StoreData> = { projects };
  return {
    get: <K extends keyof StoreData>(key: K, fallback?: StoreData[K]) => (data[key] ?? fallback) as StoreData[K],
    set: <K extends keyof StoreData>(key: K, value: StoreData[K]) => {
      data[key] = value;
    },
    data,
  };
};

const newManager = (store: ReturnType<typeof makeStore>, onProjectsChanged?: () => void) =>
  new ProjectManager({ store: store as never, onProjectsChanged });

beforeEach(() => {
  dirs.root = mkdtempSync(path.join(tmpdir(), 'omni-projects-'));
});

afterEach(() => {
  rmSync(dirs.root, { recursive: true, force: true });
});

describe('ProjectManager', () => {
  it('seeds exactly one Personal project', () => {
    const store = makeStore();
    newManager(store);
    newManager(store);
    expect(store.data.projects?.filter((p) => p.isPersonal)).toHaveLength(1);
  });

  it('adds projects with unique slugs and a working directory', () => {
    const store = makeStore();
    const changed = vi.fn();
    const pm = newManager(store, changed);
    const first = pm.addProject({ label: 'Thesis', slug: 'thesis', sources: [] });
    const second = pm.addProject({ label: 'Thesis', slug: 'thesis', sources: [] });
    expect(first.slug).toBe('thesis');
    expect(second.slug).not.toBe('thesis');
    expect(existsSync(path.join(dirs.root, 'Workspace', 'Projects', 'thesis'))).toBe(true);
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it('renames and removes projects', () => {
    const store = makeStore();
    const pm = newManager(store);
    const project = pm.addProject({ label: 'Draft', slug: 'draft', sources: [] });
    pm.updateProject(project.id, { label: 'Final' });
    expect(store.data.projects?.find((p) => p.id === project.id)?.label).toBe('Final');
    pm.removeProject(project.id);
    expect(store.data.projects?.some((p) => p.id === project.id)).toBe(false);
  });

  it('resolves a local first source as the project dir, else the slug folder', () => {
    const store = makeStore();
    const pm = newManager(store);
    const local = pm.addProject({
      label: 'Repo',
      slug: 'repo',
      sources: [{ id: 's1', mountName: 'repo', kind: 'local', workspaceDir: '/home/u/repo' }],
    });
    const plain = pm.addProject({ label: 'Notes', slug: 'notes', sources: [] });
    expect(pm.getProjectDir(local.id)).toBe('/home/u/repo');
    expect(pm.getProjectDir(plain.id)).toBe(path.join(dirs.root, 'Workspace', 'Projects', 'notes'));
    expect(pm.getProjectDir('missing')).toBeNull();
  });

  it('registers the project channels', () => {
    const ipc = new StubIpc();
    const pm = newManager(makeStore());
    const channels = registerProjectHandlers(ipc, () => pm);
    expect(channels).toEqual([
      'project:add-project',
      'project:update-project',
      'project:remove-project',
      'project:get-dir',
      'project:check-git-repo',
    ]);
  });
});

describe('project git checkouts', () => {
  const cloneInto = (dest: string) => {
    const seed = path.join(dirs.root, `seed-${path.basename(dest)}`);
    mkdirSync(seed);
    const git = (cwd: string, ...args: string[]) =>
      execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=T', '-C', cwd, ...args], { stdio: 'pipe' });
    git(seed, 'init', '-b', 'main');
    writeFileSync(path.join(seed, 'a.txt'), 'a\n');
    git(seed, 'add', '.');
    git(seed, 'commit', '-m', 'init');
    mkdirSync(path.dirname(dest), { recursive: true });
    execFileSync('git', ['clone', seed, dest], { stdio: 'pipe' });
  };
  const remote = (id: string) => ({ id, mountName: id, kind: 'git-remote' as const, repoUrl: `https://x/${id}.git` });

  it('deletes a removed source clone unless it holds work', async () => {
    const store = makeStore();
    const pm = newManager(store);
    const project = pm.addProject({ label: 'P', slug: 'p', sources: [remote('clean'), remote('edited')] });
    const checkouts = path.join(dirs.root, 'checkouts', project.id);
    cloneInto(path.join(checkouts, 'clean'));
    cloneInto(path.join(checkouts, 'edited'));
    writeFileSync(path.join(checkouts, 'edited', 'a.txt'), 'changed\n');

    pm.updateProject(project.id, { sources: [] });
    await vi.waitFor(() => expect(existsSync(path.join(checkouts, 'clean'))).toBe(false));
    expect(existsSync(path.join(checkouts, 'edited', 'a.txt'))).toBe(true);
  });

  it('deletes a removed project and its clean clones', async () => {
    const store = makeStore();
    const pm = newManager(store);
    const project = pm.addProject({ label: 'P', slug: 'p', sources: [remote('lib')] });
    cloneInto(path.join(dirs.root, 'checkouts', project.id, 'lib'));

    pm.removeProject(project.id);
    await vi.waitFor(() => expect(existsSync(path.join(dirs.root, 'checkouts', project.id))).toBe(false));
  });
});

describe('importLegacyProjectsDb', () => {
  const writeLegacyDb = () => {
    const db = new DatabaseSync(path.join(dirs.root, 'projects.db'));
    db.exec(`CREATE TABLE projects (
      id TEXT PRIMARY KEY, label TEXT, slug TEXT, is_personal INTEGER, sources TEXT,
      pinned_at TEXT, created_at TEXT
    )`);
    db.prepare('INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)').run(
      'p1',
      'Personal',
      'personal',
      1,
      '[]',
      null,
      '2026-01-01T00:00:00.000Z'
    );
    db.prepare('INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)').run(
      'p2',
      'Repo',
      'repo',
      0,
      JSON.stringify([{ id: 's1', mountName: 'repo', kind: 'local', workspaceDir: '/r' }]),
      '2026-02-01T00:00:00.000Z',
      '2026-01-02T00:00:00.000Z'
    );
    db.close();
  };

  it('copies legacy rows into an empty store and retires the database', () => {
    writeLegacyDb();
    const store = makeStore();
    importLegacyProjectsDb(store as never);
    expect(store.data.projects).toEqual([
      {
        id: 'p1',
        label: 'Personal',
        slug: 'personal',
        sources: [],
        createdAt: Date.parse('2026-01-01'),
        isPersonal: true,
      },
      {
        id: 'p2',
        label: 'Repo',
        slug: 'repo',
        sources: [{ id: 's1', mountName: 'repo', kind: 'local', workspaceDir: '/r' }],
        createdAt: Date.parse('2026-01-02'),
        pinnedAt: Date.parse('2026-02-01'),
      },
    ]);
    expect(existsSync(path.join(dirs.root, 'projects.db'))).toBe(false);
    expect(existsSync(path.join(dirs.root, 'projects.db.imported'))).toBe(true);
  });

  it('leaves an already-populated store untouched', () => {
    writeLegacyDb();
    const existing: Project = { id: 'x', label: 'X', slug: 'x', sources: [], createdAt: 1 };
    const store = makeStore([existing]);
    importLegacyProjectsDb(store as never);
    expect(store.data.projects).toEqual([existing]);
  });
});
