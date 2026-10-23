import { Card, CardContent } from '@omniagents/ui/card';
import { memo } from 'react';

import {
  settingsCardContentClassName,
  SettingsPane,
  SettingsSection,
} from '@/renderer/features/SettingsModal/SettingsLayout';
import { SettingsModalInstallApp } from '@/renderer/features/SettingsModal/SettingsModalInstallApp';
import { SettingsModalNotifications } from '@/renderer/features/SettingsModal/SettingsModalNotifications';
import { SettingsModalOptInToLauncherPrereleases } from '@/renderer/features/SettingsModal/SettingsModalOptInToLauncherPrereleases';
import { isElectron } from '@/renderer/services/ipc';

/**
 * App-level basics only: notifications and install/updates. Appearance, AI,
 * voice, and developer concerns have their own tabs.
 */
export const SettingsModalGeneralTab = memo(() => {
  return (
    <SettingsPane>
      <SettingsSection title="Notifications">
        <Card>
          <CardContent>
            <SettingsModalNotifications />
          </CardContent>
        </Card>
      </SettingsSection>

      <SettingsSection title="Features">
        <Card>
          <CardContent className={settingsCardContentClassName}>
            {/* Renders nothing unless the browser reports installability. */}
            <SettingsModalInstallApp />
            {/* Launcher auto-update is Electron-only; server mode updates with its deployment. */}
            {isElectron && <SettingsModalOptInToLauncherPrereleases />}
          </CardContent>
        </Card>
      </SettingsSection>
    </SettingsPane>
  );
});
SettingsModalGeneralTab.displayName = 'SettingsModalGeneralTab';
