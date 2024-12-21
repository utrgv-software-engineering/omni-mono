import { Button } from '@invoke-ai/ui-library';
import { useStore } from '@nanostores/react';
import { memo } from 'react';

import { BodyContainer, BodyContent, BodyFooter, BodyHeader } from '@/renderer/common/layout';
import { InstallFlowLogs } from '@/renderer/features/InstallFlow/InstallFlowLogs';
import { InstallFlowStepper } from '@/renderer/features/InstallFlow/InstallFlowStepper';
import {
  $installProcessStatus,
  installFlowApi,
  isActiveInstallProcessStatus,
} from '@/renderer/features/InstallFlow/state';

export const InstallFlowStepInstall = memo(() => {
  const installProcessStatus = useStore($installProcessStatus);
  const isFinished = useStore(installFlowApi.$isFinished);

  const isActive = isActiveInstallProcessStatus(installProcessStatus);

  return (
    <BodyContainer>
      <BodyHeader h="min-content">
        <InstallFlowStepper />
      </BodyHeader>
      <BodyContent>
        <InstallFlowLogs />
      </BodyContent>
      <BodyFooter>
        {isActive && (
          <Button
            onClick={installFlowApi.cancelInstall}
            isLoading={installProcessStatus.type === 'canceling'}
            colorScheme="error"
            loadingText="Canceling"
          >
            Cancel
          </Button>
        )}
        {!isActive && isFinished && (
          <Button colorScheme="invokeYellow" onClick={installFlowApi.finalizeInstall}>
            Finish
          </Button>
        )}
      </BodyFooter>
    </BodyContainer>
  );
});
InstallFlowStepInstall.displayName = 'InstallFlowStepInstall';
