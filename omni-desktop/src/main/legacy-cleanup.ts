/**
 * Removes on-disk state left behind by retired features. Runs in the
 * background on every boot and is idempotent: once the leftovers are gone it
 * only lists the workspaces directory.
 *
 * - `sandbox/` and `snapshots/` held container-era runtime state and snapshot
 *   archives; nothing reads them since the desktop went host-only.
 * - `workspaces/<id>/<mount>` git checkouts were per-chat clones of a
 *   project's git-remote sources. Each project source now has one shared
 *   clone under `checkouts/`, so these are orphans. A clean clone is
 *   deleted. One holding work (uncommitted or untracked files, commits on no
 *   remote, stashes) moves to `retired-clones/<workspace>/<mount>`: left in
 *   place it would block its chat from linking the shared checkout there.
 */
import { lstat, mkdir, readdir, rename, rm } from 'node:fs/promises';
import path from 'node:path';

import { holdsWork } from '@/main/checkouts';

const RETIRED_DIRS = ['sandbox', 'snapshots'];

const isDir = async (target: string): Promise<boolean> => {
  try {
    return (await lstat(target)).isDirectory();
  } catch {
    return false;
  }
};

const orphanedClones = async (workspacesDir: string): Promise<string[]> => {
  const clones: string[] = [];
  for (const workspace of await readdir(workspacesDir).catch(() => [])) {
    const root = path.join(workspacesDir, workspace);
    if (!(await isDir(root))) {
      continue;
    }
    for (const entry of await readdir(root).catch(() => [])) {
      const mount = path.join(root, entry);
      // Mounts of local folders are links to the user's own directories;
      // only real directories here are launcher-made clones.
      if ((await isDir(mount)) && (await isDir(path.join(mount, '.git')))) {
        clones.push(mount);
      }
    }
  }
  return clones;
};

export async function removeRetiredState(configDir: string): Promise<void> {
  for (const name of RETIRED_DIRS) {
    await rm(path.join(configDir, name), { recursive: true, force: true });
  }
  const workspacesDir = path.join(configDir, 'workspaces');
  for (const clone of await orphanedClones(workspacesDir)) {
    if (await holdsWork(clone)) {
      const kept = path.join(configDir, 'retired-clones', path.relative(workspacesDir, clone));
      await mkdir(path.dirname(kept), { recursive: true });
      await rename(clone, kept);
    } else {
      await rm(clone, { recursive: true, force: true });
    }
  }
}
