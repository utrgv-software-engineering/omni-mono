import { execFile } from 'child_process';
import { ipcMain, shell } from 'electron';
import type Store from 'electron-store';
import { mkdirSync } from 'fs';
import fs from 'fs/promises';
import { nanoid } from 'nanoid';
import path from 'path';
import { promisify } from 'util';

import { slugifyUnique } from '@/lib/slugify-unique';
import { getDefaultWorkspaceDir, getProjectDir } from '@/main/util';
import type { IIpcListener } from '@/shared/ipc-listener';
import { validateProjectSources } from '@/shared/project-source';
import {
  type ArtifactFileEntry,
  firstSource,
  type GitRepoInfo,
  type Project,
  type ProjectId,
  type StoreData,
} from '@/shared/types';

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
    projects[index] = { ...projects[index]!, ...patch };
    this.setProjects(projects);
  };

  removeProject = (id: ProjectId): void => {
    this.setProjects(this.getProjects().filter((p) => p.id !== id));
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

  /** List the top level of the project's working directory, folders first. */
  listProjectFiles = async (projectId: ProjectId): Promise<ArtifactFileEntry[]> => {
    const project = this.getProjects().find((p) => p.id === projectId);
    if (!project) {
      return [];
    }
    const dir = this.getProjectDirPath(project);
    try {
      const entries = await fs.readdir(dir, { withFileTypes: true });
      const results: ArtifactFileEntry[] = [];
      for (const entry of entries) {
        try {
          const stat = await fs.stat(path.join(dir, entry.name));
          results.push({
            relativePath: entry.name,
            name: entry.name,
            isDirectory: entry.isDirectory(),
            size: stat.size,
            modifiedAt: stat.mtimeMs,
          });
        } catch {
          // Skip entries we can't stat
        }
      }
      results.sort((a, b) => {
        if (a.isDirectory !== b.isDirectory) {
          return a.isDirectory ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
      });
      return results;
    } catch {
      return [];
    }
  };

  /** Open a file from the project folder in the OS default application. */
  openProjectFile = async (projectId: ProjectId, relativePath: string): Promise<void> => {
    const project = this.getProjects().find((p) => p.id === projectId);
    if (!project) {
      return;
    }
    const dir = this.getProjectDirPath(project);
    const fullPath = path.resolve(dir, relativePath);
    // Separator-terminated prefix so dir="/tmp/proj" can't match "/tmp/proj-evil/secret".
    const normalizedDir = dir.endsWith(path.sep) ? dir : dir + path.sep;
    if (!fullPath.startsWith(normalizedDir) && fullPath !== dir) {
      throw new Error('Path traversal not allowed');
    }
    await shell.openPath(fullPath);
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
  h('project:list-project-files', (pm, projectId) => pm.listProjectFiles(projectId));
  h('project:open-project-file', (pm, projectId, relativePath) => pm.openProjectFile(projectId, relativePath));

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
