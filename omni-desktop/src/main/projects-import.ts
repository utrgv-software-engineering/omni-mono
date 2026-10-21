/**
 * One-time import of the project list from the retired `projects.db` SQLite
 * store into the electron store. Runs on boot; once it has copied the rows it
 * renames the database so it never runs again.
 */
import { DatabaseSync } from 'node:sqlite';

import { existsSync, renameSync } from 'fs';
import path from 'path';

import { getOmniConfigDir } from '@/main/util';
import type { Project, ProjectSource } from '@/shared/types';

type LegacyProjectRow = {
  id: string;
  label: string;
  slug: string;
  is_personal: number;
  sources: string;
  pinned_at: string | null;
  created_at: string;
};

const parseSources = (json: string): ProjectSource[] => {
  try {
    const parsed = JSON.parse(json) as unknown;
    return Array.isArray(parsed) ? (parsed as ProjectSource[]) : [];
  } catch {
    return [];
  }
};

const rowToProject = (row: LegacyProjectRow): Project => ({
  id: row.id,
  label: row.label,
  slug: row.slug,
  sources: parseSources(row.sources),
  createdAt: Date.parse(row.created_at) || Date.now(),
  ...(row.is_personal ? { isPersonal: true } : {}),
  ...(row.pinned_at ? { pinnedAt: Date.parse(row.pinned_at) } : {}),
});

/** Copy legacy project rows into `store.projects` when the store has none. */
export function importLegacyProjectsDb(store: {
  get(key: 'projects', defaultValue: Project[]): Project[];
  set(key: 'projects', value: Project[]): void;
}): void {
  const dbPath = path.join(getOmniConfigDir(), 'projects.db');
  if (!existsSync(dbPath)) {
    return;
  }
  try {
    if (store.get('projects', []).length === 0) {
      const db = new DatabaseSync(dbPath, { readOnly: true });
      try {
        const rows = db
          .prepare(
            'SELECT id, label, slug, is_personal, sources, pinned_at, created_at FROM projects ORDER BY created_at'
          )
          .all() as LegacyProjectRow[];
        store.set('projects', rows.map(rowToProject));
      } finally {
        db.close();
      }
    }
    renameSync(dbPath, `${dbPath}.imported`);
  } catch (err) {
    console.error('[projects-import] Failed to import projects.db:', err);
  }
}
