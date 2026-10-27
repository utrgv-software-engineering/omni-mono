/**
 * Launcher-made git checkouts: per-project clones of git-remote sources
 * (`checkouts/<project>/<source>`) and the per-chat clones they replaced.
 * A checkout is only ever deleted when it holds no work.
 */
import { execFile } from 'node:child_process';
import { readdir, rm, rmdir } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';

import { projectCheckoutPath } from '@/main/util';
import type { Project } from '@/shared/types';

const execFileAsync = promisify(execFile);

const git = async (cwd: string, args: string[]): Promise<string> =>
  (await execFileAsync('git', ['-C', cwd, ...args], { maxBuffer: 1024 * 1024 })).stdout.trim();

/** Uncommitted or untracked files, commits on no remote, or stashes. Unreadable counts as work. */
export const holdsWork = async (checkout: string): Promise<boolean> => {
  try {
    const [status, unpushed, stashes] = await Promise.all([
      git(checkout, ['status', '--porcelain']),
      git(checkout, ['log', '--branches', '--not', '--remotes', '--oneline', '-1']),
      git(checkout, ['stash', 'list', '-1']),
    ]);
    return Boolean(status || unpushed || stashes);
  } catch {
    return true;
  }
};

const exists = async (target: string): Promise<boolean> =>
  readdir(target).then(
    () => true,
    () => false
  );

/**
 * Delete the clones of `sources` that `project` no longer has (all of them
 * when the project itself is gone), keeping any that hold work.
 */
export async function discardProjectCheckouts(projectId: string, removed: Project['sources']): Promise<void> {
  for (const source of removed) {
    if (source.kind !== 'git-remote') {
      continue;
    }
    const checkout = projectCheckoutPath(projectId, source.id);
    if ((await exists(checkout)) && !(await holdsWork(checkout))) {
      await rm(checkout, { recursive: true, force: true });
      await rm(path.join(path.dirname(checkout), `.${source.id}.lock`), { force: true });
    }
  }
  // Drop the project's checkouts folder once nothing is left in it.
  await rmdir(path.dirname(projectCheckoutPath(projectId, '_'))).catch(() => {});
}
