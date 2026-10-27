import { atom } from 'nanostores';

import { persistedStoreApi } from '@/renderer/services/store';
import type { ProjectId } from '@/shared/types';

/** Tabs of the project shell. */
export type ProjectTab = 'home' | 'settings';

/** The project the Projects surface is showing, or null before one is picked. */
export type ProjectView = { projectId: ProjectId; tab: ProjectTab } | null;

export const $projectView = atom<ProjectView>(null);

/** Open a project (raising the Projects surface). */
export const goToProject = (projectId: ProjectId, tab: ProjectTab = 'home'): void => {
  if (persistedStoreApi.$atom.get().layoutMode !== 'work') {
    void persistedStoreApi.setKey('layoutMode', 'work');
  }
  $projectView.set({ projectId, tab });
};
