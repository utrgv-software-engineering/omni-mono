/**
 * Pure catalog builder for `list_apps` — no live-store / IPC imports, so it is
 * unit-testable in isolation. The live wrapper that gathers `$liveApps`,
 * `$agentStatuses`, and the persisted store lives in `app-catalog.ts`.
 *
 * `list_apps` must report every app *available* in the caller's scope, each
 * annotated with whether it is currently *running* (a live webview is mounted).
 * The live registry only knows what's mounted, and dock apps mount lazily (one
 * non-chat app per column at a time), so this merges the available catalog
 * with the running set.
 */
import type { AppHandleId, AppHandleScope, AppScopeFilter, LiveAppSnapshot } from '@/shared/app-control-types';
import { isControllableKind, makeAppHandleId } from '@/shared/app-control-types';
import type { AppDescriptor, AppId, AppKind } from '@/shared/app-registry';
import type { CodeTab } from '@/shared/types';

/** One row of `list_apps` output (pre-serialization). */
export type CatalogApp = {
  id: AppId;
  /** Canonical address. */
  handleId: AppHandleId;
  kind: AppKind;
  scope: AppHandleScope;
  label: string;
  controllable: boolean;
  /** A live webview is currently mounted for this app. */
  running: boolean;
  /** The app can be opened in this scope (sandbox services present, etc.). */
  available: boolean;
  url?: string;
  title?: string;
  /** Set for column-scoped apps. */
  tabId?: string;
};

export type CatalogInput = {
  filter: AppScopeFilter;
  live: Record<AppHandleId, LiveAppSnapshot>;
  registry: AppDescriptor[];
  /** Session columns (excludes standalone app columns). */
  codeTabs: CodeTab[];
};

/** Pure: merge the available catalog with the running set for a scope. */
export function buildAppCatalog(input: CatalogInput): CatalogApp[] {
  const { filter, live, registry, codeTabs } = input;
  const out: CatalogApp[] = [];

  const columns: CodeTab[] = filter.tabId ? codeTabs.filter((t) => t.id === filter.tabId) : [];

  // Column-scoped apps, per relevant column.
  for (const tab of columns) {
    for (const app of registry) {
      if (!app.columnScoped || app.id === 'chat') {
        // `chat` is the agent's own conversation surface, not a drivable app.
        continue;
      }
      const handleId = makeAppHandleId('column', app.id, tab.id);
      const liveEntry = live[handleId];
      out.push({
        id: app.id,
        handleId,
        kind: app.kind,
        scope: 'column',
        label: app.label,
        controllable: liveEntry ? liveEntry.controllable : isControllableKind(app.kind),
        running: !!liveEntry,
        available: true,
        url: liveEntry?.url,
        title: liveEntry?.title,
        tabId: tab.id,
      });
    }
  }

  // Global dock apps.
  if (filter.allowGlobal) {
    for (const app of registry) {
      if (app.columnScoped) {
        continue;
      }
      const handleId = makeAppHandleId('global', app.id);
      const liveEntry = live[handleId];
      out.push({
        id: app.id,
        handleId,
        kind: app.kind,
        scope: 'global',
        label: app.label,
        controllable: liveEntry ? liveEntry.controllable : isControllableKind(app.kind),
        running: !!liveEntry,
        available: true,
        url: liveEntry?.url,
        title: liveEntry?.title,
      });
    }
    // Live global entries the registry doesn't cover (e.g. the shell's global
    // dock browser, `global:browser`).
    for (const entry of Object.values(live)) {
      if (entry.scope !== 'global' || out.some((o) => o.handleId === entry.handleId)) {
        continue;
      }
      out.push({
        id: entry.appId,
        handleId: entry.handleId,
        kind: entry.kind,
        scope: 'global',
        label: entry.label,
        controllable: entry.controllable,
        running: true,
        available: true,
        url: entry.url,
        title: entry.title,
      });
    }
  }

  return out;
}
