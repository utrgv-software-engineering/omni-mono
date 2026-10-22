import { useStore } from '@nanostores/react';
import { memo, useCallback, useEffect, useState } from 'react';

import { Alert, AlertDescription } from '@/renderer/ds/ui/alert';
import { Button } from '@/renderer/ds/ui/button';
import { Card, CardContent } from '@/renderer/ds/ui/card';
import { Field, FieldLabel } from '@/renderer/ds/ui/field';
import { $launcherVersion } from '@/renderer/features/Banner/state';
import { $omniInstallProcessStatus, $omniRuntimeInfo, omniInstallApi } from '@/renderer/features/Omni/state';
import { RemoteBackendCard } from '@/renderer/features/SettingsModal/RemoteBackendCard';
import {
  settingsCardContentClassName,
  SettingsPane,
  SettingsSection,
} from '@/renderer/features/SettingsModal/SettingsLayout';
import { emitter, isElectron } from '@/renderer/services/ipc';
import { persistedStoreApi, selectWorkspaceDir } from '@/renderer/services/store';

/**
 * Developer band: where sessions run and what runs them — workspace
 * directory, the omni runtime, and the CLI symlink.
 */
export const SettingsModalWorkspaceTab = memo(() => {
  const store = useStore(persistedStoreApi.$atom);
  const runtimeInfo = useStore($omniRuntimeInfo);
  const installStatus = useStore($omniInstallProcessStatus);
  const launcherVersion = useStore($launcherVersion);

  const isInstalling = installStatus.type === 'starting' || installStatus.type === 'installing';

  const [cliInPath, setCliInPath] = useState<{ installed: boolean; symlinkPath: string } | null>(null);
  const [cliInstalling, setCliInstalling] = useState(false);
  const [cliError, setCliError] = useState<string | null>(null);

  const checkCliStatus = useCallback(async () => {
    const status = await emitter.invoke('util:get-cli-in-path-status');
    setCliInPath(status);
  }, []);

  useEffect(() => {
    checkCliStatus();
  }, [checkCliStatus, runtimeInfo]);

  const installCliToPath = useCallback(async () => {
    setCliInstalling(true);
    setCliError(null);
    try {
      const result = await emitter.invoke('util:install-cli-to-path');
      if (!result.success) {
        setCliError(result.error);
      }
      await checkCliStatus();
    } finally {
      setCliInstalling(false);
    }
  }, [checkCliStatus]);

  const restoreDefaultWorkspaceDir = useCallback(async () => {
    const defaultDir = await emitter.invoke('util:get-default-workspace-dir');
    await emitter.invoke('util:ensure-directory', defaultDir);
    await persistedStoreApi.setKey('workspaceDir', defaultDir);
  }, []);

  const reinstallRuntime = useCallback(() => {
    omniInstallApi.startInstall(true);
  }, []);

  return (
    <SettingsPane>
      {isElectron && (
        <SettingsSection title="Backend">
          <RemoteBackendCard />
        </SettingsSection>
      )}

      <SettingsSection title="Workspace">
        <Card>
          <CardContent>
            <Field orientation="horizontal" className="justify-between gap-4">
              <div className="min-w-0">
                <FieldLabel>Workspace directory</FieldLabel>
              </div>
              <span className="text-sm text-muted-foreground overflow-hidden text-ellipsis whitespace-nowrap max-w-50 sm:text-xs">
                {store.workspaceDir ?? 'Default'}
              </span>
              {/* Picking a folder needs the native dialog; the browser build
                    can only restore the backend's default. */}
              {isElectron ? (
                <Button size="sm" variant="ghost" onClick={selectWorkspaceDir}>
                  Change
                </Button>
              ) : (
                !store.workspaceDir && (
                  <Button size="sm" variant="ghost" onClick={restoreDefaultWorkspaceDir}>
                    Use default
                  </Button>
                )
              )}
            </Field>
          </CardContent>
        </Card>
      </SettingsSection>

      {/* Runtime install + CLI-in-PATH are host operations; server mode manages its own runtime. */}
      {isElectron && (
        <SettingsSection title="Runtime">
          <Card>
            <CardContent className={settingsCardContentClassName}>
              <Field orientation="horizontal" className="justify-between gap-4">
                <div className="min-w-0">
                  <FieldLabel>{`Runtime${runtimeInfo.isInstalled ? ` (v${runtimeInfo.version})` : ''}`}</FieldLabel>
                </div>
                <Button size="sm" variant="ghost" onClick={reinstallRuntime} disabled={isInstalling}>
                  {isInstalling
                    ? runtimeInfo.isInstalled
                      ? 'Reinstalling…'
                      : 'Installing…'
                    : runtimeInfo.isInstalled
                      ? 'Reinstall'
                      : 'Install'}
                </Button>
              </Field>
              <Field orientation="horizontal" className="justify-between gap-4">
                <div className="min-w-0">
                  <FieldLabel>&apos;omni&apos; command in PATH</FieldLabel>
                </div>
                {cliInPath?.installed ? (
                  <span className="text-sm text-muted-foreground sm:text-xs">Installed</span>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={installCliToPath}
                    disabled={!runtimeInfo.isInstalled || cliInstalling}
                  >
                    {cliInstalling ? 'Installing…' : 'Install'}
                  </Button>
                )}
              </Field>
              {cliError && (
                <Alert variant="destructive">
                  <AlertDescription>{cliError}</AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        </SettingsSection>
      )}

      <SettingsSection title="About">
        <Card>
          <CardContent className={settingsCardContentClassName}>
            <Field orientation="horizontal" className="justify-between gap-4">
              <div className="min-w-0">
                <FieldLabel>Launcher version</FieldLabel>
              </div>
              <span className="text-sm text-muted-foreground sm:text-xs">{launcherVersion ?? '—'}</span>
            </Field>
          </CardContent>
        </Card>
      </SettingsSection>
    </SettingsPane>
  );
});
SettingsModalWorkspaceTab.displayName = 'SettingsModalWorkspaceTab';
