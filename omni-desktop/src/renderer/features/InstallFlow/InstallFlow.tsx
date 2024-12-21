import { useStore } from '@nanostores/react';
import { memo } from 'react';
import { ErrorBoundary } from 'react-error-boundary';
import { assert } from 'tsafe';

import { ErrorBoundaryFallback } from '@/renderer/app/ErrorBoundaryFallback';
import { InstallFlowStepConfigure } from '@/renderer/features/InstallFlow/InstallFlowStepConfigure';
import { InstallFlowStepInstall } from '@/renderer/features/InstallFlow/InstallFlowStepInstall';
import { InstallFlowStepLocation } from '@/renderer/features/InstallFlow/InstallFlowStepLocation';
import { InstallFlowStepReview } from '@/renderer/features/InstallFlow/InstallFlowStepReview';
import { InstallFlowStepVersion } from '@/renderer/features/InstallFlow/InstallFlowStepVersion';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';

const InstallFlowContent = memo(() => {
  const activeStep = useStore(installFlowApi.$activeStep);

  switch (activeStep) {
    case 0:
      return <InstallFlowStepLocation />;
    case 1:
      return <InstallFlowStepVersion />;
    case 2:
      return <InstallFlowStepConfigure />;
    case 3:
      return <InstallFlowStepReview />;
    case 4:
    case 5: // This is a hack to show the install step as complete when the install is finished
      return <InstallFlowStepInstall />;
    default:
      assert(false, 'Invalid step');
  }
});
InstallFlowContent.displayName = 'InstallFlowContent';

export const InstallFlow = memo(() => {
  return (
    <ErrorBoundary FallbackComponent={ErrorBoundaryFallback} onReset={installFlowApi.cancelFlow}>
      <InstallFlowContent />
    </ErrorBoundary>
  );
});
InstallFlow.displayName = 'InstallFlow';
