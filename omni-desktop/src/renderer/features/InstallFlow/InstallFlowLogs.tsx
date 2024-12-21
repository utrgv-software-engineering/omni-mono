import { useStore } from '@nanostores/react';
import { memo } from 'react';

import {
  $installProcessLogs,
  $installProcessStatus,
  isActiveInstallProcessStatus,
} from '@/renderer/features/InstallFlow/state';
import { LogViewer } from '@/renderer/features/LogViewer/LogViewer';
import { LogViewerStatusIndicator } from '@/renderer/features/LogViewer/LogViewerStatusIndicator';

export const InstallFlowLogs = memo(() => {
  const installProcessLogs = useStore($installProcessLogs);
  const installProcessStatus = useStore($installProcessStatus);
  return (
    <LogViewer logs={installProcessLogs}>
      <LogViewerStatusIndicator status={installProcessStatus} getIsActive={isActiveInstallProcessStatus} />
    </LogViewer>
  );
});
InstallFlowLogs.displayName = 'InstallFlowLogs';
