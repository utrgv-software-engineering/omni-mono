import { useStore } from '@nanostores/react';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@omniagents/ui/empty';
import { memo, useMemo } from 'react';

import { TopAppBar } from '@/renderer/ds/TopAppBar';
import { persistedStoreApi } from '@/renderer/services/store';

import { ProjectHome } from './ProjectHome';
import { ProjectSettings } from './ProjectSettings';
import { ProjectShell } from './ProjectShell';
import { $projectView } from './view-state';

/**
 * The Projects surface: one project's shell (Home / Settings). The unified
 * AppSidebar picks the project; on mobile the TopAppBar carries the drawer
 * handle.
 */
export const ProjectsSurface = memo(() => {
  const store = useStore(persistedStoreApi.$atom);
  const view = useStore($projectView);
  const project = useMemo(
    () => (view ? store.projects.find((candidate) => candidate.id === view.projectId) : undefined),
    [view, store.projects]
  );

  return (
    <div className="flex h-full w-full min-w-0 flex-col">
      <div className="shrink-0 sm:hidden">
        <TopAppBar title={project?.label ?? 'Projects'} showMenu className="bg-card" />
      </div>
      <div className="min-h-0 flex-1">
        {view ? (
          <ProjectShell projectId={view.projectId} activeTab={view.tab}>
            {view.tab === 'home' ? (
              <ProjectHome projectId={view.projectId} />
            ) : (
              <ProjectSettings projectId={view.projectId} />
            )}
          </ProjectShell>
        ) : (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyTitle>No project selected</EmptyTitle>
              <EmptyDescription>Pick a project in the sidebar, or create one.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </div>
    </div>
  );
});
ProjectsSurface.displayName = 'ProjectsSurface';
