/**
 * Live wrapper around the pure catalog builder (`app-catalog-core.ts`): gathers
 * `$liveApps` and the persisted store, then builds the `list_apps` catalog for the caller's scope.
 */
import { buildAppCatalog, type CatalogApp } from '@/renderer/features/AppControl/app-catalog-core';
import { $liveApps } from '@/renderer/features/AppControl/live-registry';
import { persistedStoreApi } from '@/renderer/services/store';
import type { AppScopeFilter } from '@/shared/app-control-types';
import { APP_REGISTRY } from '@/shared/app-registry';

export type { CatalogApp } from '@/renderer/features/AppControl/app-catalog-core';
export { buildAppCatalog } from '@/renderer/features/AppControl/app-catalog-core';

/** Gather live state and build the catalog for the caller's scope. */
export function listAppsForScope(filter: AppScopeFilter): CatalogApp[] {
  const store = persistedStoreApi.get();
  const registry = APP_REGISTRY;

  const codeTabs = store.codeTabs ?? [];

  return buildAppCatalog({
    filter,
    live: $liveApps.get(),
    registry,
    codeTabs,
  });
}
