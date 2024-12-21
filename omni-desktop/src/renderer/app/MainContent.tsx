import { useStore } from '@nanostores/react';
import { memo } from 'react';

import { FirstRun } from '@/renderer/app/NoInstallSelected';
import { InstallFlow } from '@/renderer/features/InstallFlow/InstallFlow';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';
import { LaunchFlow } from '@/renderer/features/LaunchFlow/LaunchFlow';
import { $installDirDetails } from '@/renderer/services/store';

export const MainContent = memo(() => {
  const isInstallFlowStarted = useStore(installFlowApi.$isStarted);
  const installDirDetails = useStore($installDirDetails);

  if (isInstallFlowStarted) {
    return <InstallFlow />;
  }

  if (!installDirDetails) {
    return <FirstRun />;
  }

  return <LaunchFlow installDirDetails={installDirDetails} />;
});
MainContent.displayName = 'MainContent';
