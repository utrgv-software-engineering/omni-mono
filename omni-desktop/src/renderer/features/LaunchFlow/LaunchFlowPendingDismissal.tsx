import { Button } from '@invoke-ai/ui-library';
import { memo } from 'react';

import { BodyContainer, BodyContent, BodyFooter } from '@/renderer/common/layout';
import { LaunchFlowLogViewer } from '@/renderer/features/LaunchFlow/LaunchFlowLogViewer';
import { $isInvokeProcessPendingDismissal } from '@/renderer/features/LaunchFlow/state';

const dismissPostInvoke = () => {
  $isInvokeProcessPendingDismissal.set(false);
};

export const LaunchFlowPendingDismissal = memo(() => {
  return (
    <BodyContainer>
      <BodyContent>
        <LaunchFlowLogViewer />
      </BodyContent>
      <BodyFooter>
        <Button variant="ghost" onClick={dismissPostInvoke}>
          Back
        </Button>
      </BodyFooter>
    </BodyContainer>
  );
});

LaunchFlowPendingDismissal.displayName = 'LaunchFlowPendingDismissal';
