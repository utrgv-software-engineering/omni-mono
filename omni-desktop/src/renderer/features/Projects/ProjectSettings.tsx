import { useStore } from '@nanostores/react';
import { Input } from '@omniagents/ui/input';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';

import { persistedStoreApi } from '@/renderer/services/store';
import type { ProjectId } from '@/shared/types';

import { ProjectPageHeader } from './ProjectPageHeader';
import { ProjectSourcesSettings } from './ProjectSourcesSettings';
import { projectsApi } from './state';

/**
 * The shell's Settings tab — the single place a project is configured.
 * Fields save on change/blur.
 */
export const ProjectSettings = memo(({ projectId }: { projectId: ProjectId }) => {
  const store = useStore(persistedStoreApi.$atom);
  const project = useMemo(() => store.projects.find((p) => p.id === projectId), [store.projects, projectId]);

  const [name, setName] = useState(project?.label ?? '');

  // Keep the name buffer in sync with external renames (sidebar).
  const projectLabel = project?.label;
  useEffect(() => {
    if (projectLabel !== undefined) {
      setName(projectLabel);
    }
  }, [projectLabel]);

  const handleNameChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value), []);
  const handleNameBlur = useCallback(() => {
    const trimmed = name.trim();
    if (trimmed && project && trimmed !== project.label) {
      void projectsApi.updateProject(projectId, { label: trimmed });
    }
  }, [name, project, projectId]);
  const handleNameKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        handleNameBlur();
      }
    },
    [handleNameBlur]
  );

  if (!project) {
    return null;
  }

  return (
    <div className="h-full overflow-y-auto" data-slot="project-settings">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-7 px-6 py-6">
        <ProjectPageHeader title="Settings" className="pl-0 pr-0 pt-0 pb-0" />

        {/* General */}
        <div className="flex flex-col gap-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">General</span>
          <div className="flex flex-col gap-1 max-w-sm">
            <label className="text-xs text-muted-foreground">Name</label>
            <Input
              aria-label="Project name"
              type="text"
              value={name}
              onChange={handleNameChange}
              onBlur={handleNameBlur}
              onKeyDown={handleNameKeyDown}
            />
          </div>
        </div>

        <ProjectSourcesSettings projectId={projectId} />
      </div>
    </div>
  );
});
ProjectSettings.displayName = 'ProjectSettings';
