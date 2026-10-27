import { execFile } from 'child_process';
import { ipcMain } from 'electron';
import type Store from 'electron-store';
import { mkdirSync } from 'fs';
import { nanoid } from 'nanoid';
import { promisify } from 'util';

import { slugifyUnique } from '@/lib/slugify-unique';
import { discardProjectCheckouts } from '@/main/checkouts';
import { getDefaultWorkspaceDir, getProjectDir } from '@/main/util';
import type { IIpcListener } from '@/shared/ipc-listener';
import { validateProjectSources } from '@/shared/project-source';
import { firstSource, type GitRepoInfo, type Project, type ProjectId, type StoreData } from '@/shared/types';

const execFileAsync = promisify(execFile);

/**
 * Owns the project list (persisted in the electron store) and each project's
 * on-disk working directory. A project is a named set of sources plus the
 * chats attached to it.
 */
export class ProjectManager {
  private store: Store<StoreData>;

  /** Broadcast hook for shells whose store doesn't announce its own writes (server mode). */
  private onProjectsChanged?: () => void;

  constructor(arg: { store: Store<StoreData>; onProjectsChanged?: () => void }) {
    this.store = arg.store;
    this.onProjectsChanged = arg.onProjectsChanged;
    this.ensurePersonalProject();
  }

  /** Every install has exactly one Personal project; seed it on first boot. */
  private ensurePersonalProject = (): void => {
    const projects = this.getProjects();
    if (projects.some((p) => p.isPersonal)) {
      return;
    }
    this.store.set('projects', [
      ...projects,
      { id: nanoid(), label: 'Personal', slug: 'personal', isPersonal: true, sources: [], createdAt: Date.now() },
    ]);
  };

  /**
   * Resolve a project's working directory: its first source when that is a
   * local folder, otherwise the slug-based project folder.
   */
  getProjectDir = (projectId: ProjectId): string | null => {
    const project = this.getProjects().find((p) => p.id === projectId);
    if (!project) {
      return null;
    }
    const source = firstSource(project);
    return source?.kind === 'local' ? source.workspaceDir : this.getProjectDirPath(project);
  };

  // #region Projects

  private getProjects = (): Project[] => this.store.get('projects', []);

  private setProjects = (projects: Project[]): void => {
    this.store.set('projects', projects);
    this.onProjectsChanged?.();
  };

  addProject = (input: Omit<Project, 'id' | 'createdAt'>): Project => {
    const existing = this.getProjects();
    const takenSlugs = new Set(existing.map((p) => p.slug));
    const project: Project = {
      ...input,
      slug: slugifyUnique(input.slug, (s) => takenSlugs.has(s)),
      id: nanoid(),
      createdAt: Date.now(),
    };
    this.setProjects([...existing, project]);
    // Context-only named projects need a host dir so the first agent launch
    // passes AgentProcess's `isDirectory(workspaceDir)` check. Personal uses
    // the workspace root; sourced projects use the user's dirs or clone.
    if (!project.isPersonal && project.sources.length === 0) {
      mkdirSync(this.getProjectDirPath(project), { recursive: true });
    }
    return project;
  };

  updateProject = (id: ProjectId, patch: Partial<Omit<Project, 'id' | 'createdAt'>>): void => {
    const projects = this.getProjects();
    const index = projects.findIndex((p) => p.id === id);
    if (index === -1) {
      return;
    }
    if (patch.sources) {
      validateProjectSources(patch.sources);
    }
    const previous = projects[index]!;
    projects[index] = { ...previous, ...patch };
    this.setProjects(projects);
    if (patch.sources) {
      const kept = new Set(patch.sources.map((source) => source.id));
      this.discardCheckouts(
        id,
        previous.sources.filter((source) => !kept.has(source.id))
      );
    }
  };

  removeProject = (id: ProjectId): void => {
    const project = this.getProjects().find((p) => p.id === id);
    this.setProjects(this.getProjects().filter((p) => p.id !== id));
    if (project) {
      this.discardCheckouts(id, project.sources);
    }
  };

  /** A removed git source's clone goes with it, unless it holds work. */
  private discardCheckouts = (id: ProjectId, removed: Project['sources']): void => {
    void discardProjectCheckouts(id, removed).catch((error) => {
      console.warn(`[project-manager] checkout cleanup for ${id} failed:`, error);
    });
  };

  // #endregion

  // #region Project directories

  /** Personal projects use the workspace root; named projects use `Projects/<slug>/`. */
  private getProjectDirPath = (project: Project): string => {
    if (project.isPersonal) {
      return getDefaultWorkspaceDir();
    }
    return getProjectDir(project.slug);
  };

  // #endregion
}

export const checkGitRepo = async (workspaceDir: string): Promise<GitRepoInfo> => {
  try {
    await execFileAsync('git', ['-C', workspaceDir, 'rev-parse', '--is-inside-work-tree'], {
      encoding: 'utf8',
      timeout: 5_000,
    });
  } catch {
    return { isGitRepo: false };
  }
  try {
    const [branchResult, currentResult] = await Promise.all([
      execFileAsync('git', ['-C', workspaceDir, 'branch', '--list', '--format=%(refname:short)'], {
        encoding: 'utf8',
        timeout: 5_000,
      }),
      execFileAsync('git', ['-C', workspaceDir, 'branch', '--show-current'], { encoding: 'utf8', timeout: 5_000 }),
    ]);
    const branches = branchResult.stdout
      .split('\n')
      .map((b) => b.trim())
      .filter(Boolean);
    return { isGitRepo: true, branches, currentBranch: currentResult.stdout.trim() };
  } catch {
    return { isGitRepo: false };
  }
};

/**
 * IPC handlers for the project surface. Takes a `resolve(event)` callback so
 * server mode can route per connection. Returns the channel names registered
 * for cleanup.
 */
export function registerProjectHandlers(ipc: IIpcListener, resolve: (event: unknown) => ProjectManager): string[] {
  const channels: string[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const h = (ch: string, fn: (pm: ProjectManager, ...args: any[]) => unknown): void => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ipc.handle(ch, (event: unknown, ...args: any[]) => fn(resolve(event), ...args));
    channels.push(ch);
  };

  h('project:add-project', (pm, project) => pm.addProject(project));
  h('project:update-project', (pm, id, patch) => pm.updateProject(id, patch));
  h('project:remove-project', (pm, id) => pm.removeProject(id));
  h('project:get-dir', (pm, id) => pm.getProjectDir(id));
  h('project:check-git-repo', (_pm, workspaceDir) => checkGitRepo(workspaceDir));

  return channels;
}

export const createProjectManager = (arg: { ipc: IIpcListener; store: Store<StoreData> }) => {
  const projectManager = new ProjectManager({ store: arg.store });
  const channels = registerProjectHandlers(arg.ipc, () => projectManager);
  const cleanup = () => {
    for (const channel of channels) {
      ipcMain.removeHandler(channel);
    }
  };
  return [projectManager, cleanup] as const;
};
