import { useStore } from '@nanostores/react';
import { Download, Globe, PlugZap } from 'lucide-react';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';

import { useIsDesktop } from '@/renderer/common/use-is-desktop';
import { PageHeader } from '@/renderer/ds/PageHeader';
import { Button } from '@/renderer/ds/ui/button';
import { Input } from '@/renderer/ds/ui/input';
import { Skeleton } from '@/renderer/ds/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/renderer/ds/ui/toggle-group';
import { ConnectorConfigDialog } from '@/renderer/features/Plugins/ConnectorConfigDialog';
import { ExploreSection, FEATURED_MARKETPLACES, installKeyOf } from '@/renderer/features/Plugins/ExploreSection';
import { InstalledSection } from '@/renderer/features/Plugins/InstalledSection';
import { MarketplaceDialog } from '@/renderer/features/Plugins/MarketplaceDialog';
import {
  mcpConfigFromSnapshots,
  mcpCreateInput,
  mcpUpdateInput,
  storedMcpSecretKeys,
} from '@/renderer/features/Plugins/mcp-config-adapter';
import type { ExplorePlugin, PluginKind } from '@/renderer/features/Plugins/plugin-cards';
import {
  buildExplorePlugins,
  buildInstalledPlugins,
  collectDriftedItems,
  filterPlugins,
  mergeConnectorUpdate,
} from '@/renderer/features/Plugins/plugin-cards';
import { $pluginsInitialFilter } from '@/renderer/features/Plugins/plugins-nav';
import { useFeaturedManifests, usePluginsData } from '@/renderer/features/Plugins/state';
import {
  useProductManagementRefresh,
  useProductManagementSnapshot,
} from '@/renderer/omniagents-ui/product-management-context';
import { emitter, isElectron } from '@/renderer/services/ipc';
import { managementAdminApi } from '@/renderer/services/management-admin';
import type { BundleUpdateInfo, McpServerEntry } from '@/shared/types';

const SKILL_FILE_FILTERS = [{ name: 'Skill packages', extensions: ['skill'] }];

/** Referentially stable — useFeaturedManifests keys its fetch effect on this. */
const FEATURED_REPOS = FEATURED_MARKETPLACES.map((m) => m.repo);

const FILTER_OPTIONS: { value: PluginKind | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'connector', label: 'Connectors' },
  { value: 'skill', label: 'Skills' },
];

export const PluginsView = memo(() => {
  const data = usePluginsData();
  const managementSnapshot = useProductManagementSnapshot();
  const refreshManagement = useProductManagementRefresh();
  const isDesktop = useIsDesktop();

  const [filter, setFilter] = useState<PluginKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [installingKey, setInstallingKey] = useState<string | null>(null);
  const [marketplaceOpen, setMarketplaceOpen] = useState(false);
  /** `serverId: null` = create-new (name editable in the dialog). */
  const [connectorDialog, setConnectorDialog] = useState<{ serverId: string | null } | null>(null);

  // Deep link (openPlugins('connector') etc.): consume the one-shot filter
  // and clear it. The page never unmounts, hence the atom.
  const initialFilter = useStore($pluginsInitialFilter);
  useEffect(() => {
    if (initialFilter) {
      setFilter(initialFilter);
      $pluginsInitialFilter.set(null);
    }
  }, [initialFilter]);

  const { refresh } = data;

  /** The Omniagents runtime owns MCP configuration: reads and writes go
   * through its per-server RPCs. */
  const effectiveMcpConfig = useMemo(
    () => mcpConfigFromSnapshots(managementSnapshot.mcp.data?.servers ?? []),
    [managementSnapshot.mcp.data?.servers]
  );

  const installed = useMemo(
    () =>
      buildInstalledPlugins({
        mcpConfig: effectiveMcpConfig,
        skills: data.skills,
        updates: data.updates,
        runtimeMcpServers: managementSnapshot.mcp.data?.servers,
      }),
    [effectiveMcpConfig, data.skills, data.updates, managementSnapshot.mcp.data?.servers]
  );
  const filteredInstalled = useMemo(() => filterPlugins(installed, filter, query), [installed, filter, query]);

  const ctx = useMemo(
    () => ({ mcpConfig: effectiveMcpConfig, skills: data.skills, updates: data.updates }),
    [effectiveMcpConfig, data.skills, data.updates]
  );

  const manifests = useFeaturedManifests(FEATURED_REPOS);

  /** Every featured catalog's items with installed/drift state — page-level so "Update all" sees them. */
  const exploreItems = useMemo(
    () => manifests.flatMap((m) => (m.manifest ? buildExplorePlugins(m.repo, m.manifest, ctx) : [])),
    [manifests, ctx]
  );
  const driftedItems = useMemo(() => collectDriftedItems(exploreItems), [exploreItems]);

  const saveConnector = useCallback(
    async (id: string, entry: McpServerEntry) => {
      const runtime = managementSnapshot.mcp.data?.servers.find((server) => server.server_name === id);
      if (runtime?.read_only) {
        throw new Error(`MCP server "${id}" is managed by the Omniagents host and cannot be changed.`);
      }
      if (runtime) {
        await managementAdminApi.updateMcpServer(id, mcpUpdateInput(entry, runtime));
      } else {
        await managementAdminApi.createMcpServer(mcpCreateInput(id, entry));
      }
      await refreshManagement();
      await refresh();
    },
    [managementSnapshot.mcp.data?.servers, refresh, refreshManagement]
  );

  const removeConnector = useCallback(
    async (id: string) => {
      const runtime = managementSnapshot.mcp.data?.servers.find((server) => server.server_name === id);
      if (runtime?.read_only) {
        throw new Error(`MCP server "${id}" is managed by the Omniagents host and cannot be removed.`);
      }
      await managementAdminApi.deleteMcpServer(id);
      await refreshManagement();
      await refresh();
    },
    [managementSnapshot.mcp.data?.servers, refresh, refreshManagement]
  );

  const handleInstall = useCallback(
    async (item: ExplorePlugin, mode: 'install' | 'update') => {
      setActionError(null);
      setInstallingKey(installKeyOf(item));
      try {
        if (item.kind === 'connector') {
          if (mode === 'update') {
            // Take the upstream transport config but keep the env/header
            // values the user filled in (API keys, tokens).
            const existing = effectiveMcpConfig?.mcpServers[item.connector.id];
            const merged = existing ? mergeConnectorUpdate(existing, item.connector.server) : item.connector.server;
            await saveConnector(item.connector.id, merged);
          } else {
            await saveConnector(item.connector.id, item.connector.server);
          }
        } else {
          const channel = mode === 'update' ? 'skills:update-marketplace-plugin' : 'skills:install-marketplace-plugin';
          await emitter.invoke(channel, item.repo, item.plugin.name);
          await refresh();
        }
      } catch (e) {
        setActionError(e instanceof Error ? e.message : `Failed to ${mode} plugin`);
      } finally {
        setInstallingKey(null);
      }
    },
    [effectiveMcpConfig, saveConnector, refresh]
  );

  /** Installed bundles with an upstream update, one entry per bundle. */
  const pendingUpdates = useMemo(
    () => Object.values(data.updates).filter((u) => u.status === 'update-available'),
    [data.updates]
  );

  const handleUpdateAll = useCallback(async () => {
    setActionError(null);
    setInstallingKey('update-all');
    try {
      for (const u of pendingUpdates) {
        await emitter.invoke('skills:update-marketplace-plugin', u.repo, u.plugin);
      }

      // Drifted connectors use the same canonical per-server path as the
      // dialog, preserving write-only secret markers across each update.
      const connectors = driftedItems.filter((p) => p.kind === 'connector');
      if (connectors.length > 0) {
        for (const c of connectors) {
          const existing = effectiveMcpConfig?.mcpServers[c.connector.id];
          const merged = existing ? mergeConnectorUpdate(existing, c.connector.server) : c.connector.server;
          await saveConnector(c.connector.id, merged);
        }
      }
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to update plugins');
    } finally {
      setInstallingKey(null);
      await refresh();
    }
  }, [effectiveMcpConfig, pendingUpdates, driftedItems, refresh, saveConnector]);

  const handleUpdateBundle = useCallback(
    async (update: BundleUpdateInfo) => {
      setActionError(null);
      setInstallingKey(`skill:${update.repo}:${update.plugin}`);
      try {
        await emitter.invoke('skills:update-marketplace-plugin', update.repo, update.plugin);
        await refresh();
      } catch (e) {
        setActionError(e instanceof Error ? e.message : 'Failed to update plugin');
      } finally {
        setInstallingKey(null);
      }
    },
    [refresh]
  );

  const installFromFile = useCallback(async () => {
    setActionError(null);
    const filePath = await emitter.invoke('util:select-file', undefined, SKILL_FILE_FILTERS);
    if (!filePath) {
      return;
    }
    try {
      await emitter.invoke('skills:install', filePath);
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to install skill');
    }
  }, [refresh]);

  const error = actionError ?? data.error;
  const existingServerIds = Object.keys(effectiveMcpConfig?.mcpServers ?? {});
  const editedServer =
    connectorDialog?.serverId != null ? (effectiveMcpConfig?.mcpServers[connectorDialog.serverId] ?? null) : null;
  const editedRuntime =
    connectorDialog?.serverId != null
      ? managementSnapshot.mcp.data?.servers.find((server) => server.server_name === connectorDialog.serverId)
      : undefined;
  const userMcpAllowed = managementSnapshot.mcp.data?.user_mcp_allowed ?? true;

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-background">
      <PageHeader
        title="Plugins"
        showMenu={!isDesktop}
        actions={
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search plugins"
            aria-label="Search plugins"
            className="w-56 max-w-screen"
          />
        }
      />
      <div className="max-w-5xl flex flex-col gap-5 pl-5 pr-5 pb-8">
        <ToggleGroup
          type="single"
          variant="outline"
          spacing={0}
          value={filter}
          onValueChange={(value) => value && setFilter(value as PluginKind | 'all')}
          aria-label="Plugin type"
        >
          {FILTER_OPTIONS.map((option) => (
            <ToggleGroupItem key={option.value} value={option.value}>
              {option.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        {error && <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-xs">{error}</div>}

        {data.loading ? (
          <div className="flex w-full flex-col gap-5 p-4">
            {Array.from({ length: 4 }, (_, index) => (
              <div key={index} className="flex flex-col gap-2">
                <Skeleton className={`h-3 ${['w-15', 'w-18', 'w-20'][index % 3]}`} />
                <Skeleton className="h-8 w-full" />
              </div>
            ))}
          </div>
        ) : (
          <>
            <InstalledSection
              items={filteredInstalled}
              onRefresh={refresh}
              onError={setActionError}
              onConfigureConnector={(id) => setConnectorDialog({ serverId: id })}
              onRemoveConnector={removeConnector}
              onUpdateBundle={handleUpdateBundle}
              installingKey={installingKey}
              updateAllCount={pendingUpdates.length + driftedItems.length}
              onUpdateAll={handleUpdateAll}
            />

            {FEATURED_MARKETPLACES.map((marketplace, i) => (
              <ExploreSection
                key={marketplace.repo}
                marketplace={marketplace}
                manifest={manifests[i]?.manifest ?? null}
                failed={manifests[i]?.failed ?? false}
                filter={filter}
                query={query}
                ctx={ctx}
                installingKey={installingKey}
                onInstall={handleInstall}
              />
            ))}

            <div className="flex flex-col gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Add your own</span>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConnectorDialog({ serverId: null })}
                  disabled={!userMcpAllowed}
                  title={userMcpAllowed ? undefined : 'User MCP servers are disabled by the host'}
                >
                  <PlugZap className="mr-1" />
                  Add MCP server
                </Button>
                {/* Local-file picker is desktop-only — the server has no client filesystem. */}
                {isElectron && (
                  <Button size="sm" variant="ghost" onClick={installFromFile}>
                    <Download className="mr-1" />
                    Install skill from file
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={() => setMarketplaceOpen(true)}>
                  <Globe className="mr-1" />
                  Browse a marketplace
                </Button>
              </div>
            </div>
          </>
        )}
      </div>

      <ConnectorConfigDialog
        open={connectorDialog !== null}
        serverId={connectorDialog?.serverId ?? null}
        initial={editedServer}
        existingIds={existingServerIds}
        storedSecrets={storedMcpSecretKeys(editedRuntime)}
        onSave={saveConnector}
        onClose={() => setConnectorDialog(null)}
      />
      <MarketplaceDialog
        open={marketplaceOpen}
        onClose={() => setMarketplaceOpen(false)}
        ctx={ctx}
        installingKey={installingKey}
        onInstall={handleInstall}
      />
    </div>
  );
});
PluginsView.displayName = 'PluginsView';
