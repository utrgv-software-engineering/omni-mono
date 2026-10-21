/**
 * IPC handler registration for the pages surface (markdown).
 *
 * Takes a `resolve(event)` callback (see registerMilestoneHandlers).
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import type { PageManager } from '@/main/page-manager';
import type { IIpcListener } from '@/shared/ipc-listener';

export function registerPageHandlers(ipc: IIpcListener, resolve: (event: unknown) => PageManager): string[] {
  const channels: string[] = [];
  const h = (ch: string, fn: (m: PageManager, event: unknown, ...args: any[]) => unknown): void => {
    ipc.handle(ch, (event: unknown, ...args: any[]) => fn(resolve(event), event, ...args));
    channels.push(ch);
  };

  h('page:get-items', (m, _e, projectId) => m.getByProject(projectId));
  h('page:get-all', (m) => m.getAll());
  h('page:add-item', (m, _e, item, template) => m.add(item, template));
  h('page:update-item', (m, _e, id, patch) => m.update(id, patch));
  h('page:remove-item', (m, _e, id) => m.remove(id));
  h('page:read-content', (m, _e, pageId) => m.readContent(pageId));
  h('page:write-content', (m, _e, pageId, content) => m.writeContent(pageId, content));
  h('page:reorder', (m, _e, pageId, newParentId, newSortOrder) => m.reorder(pageId, newParentId, newSortOrder));
  h('page:watch', (m, _e, pageId) => m.watch(pageId));
  h('page:unwatch', (m, _e, pageId) => m.unwatch(pageId));
  return channels;
}
