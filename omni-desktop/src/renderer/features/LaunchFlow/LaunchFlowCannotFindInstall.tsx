import { Button, Divider, Heading, Text } from '@invoke-ai/ui-library';
import { memo, useCallback } from 'react';

import { BodyContainer, BodyContent, BodyFooter, BodyHeader } from '@/renderer/common/layout';
import { Strong } from '@/renderer/common/Strong';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';
import { useSelectInstall } from '@/renderer/hooks/use-select-install';
import type { DirDetails } from '@/shared/types';

type Props = {
  installDirDetails: Extract<DirDetails, { isInstalled: false }>;
};

export const LaunchFlowInvalidInstall = memo(({ installDirDetails }: Props) => {
  const selectInstall = useSelectInstall();

  const install = useCallback(() => {
    installFlowApi.beginFlow(installDirDetails);
  }, [installDirDetails]);

  return (
    <BodyContainer>
      <BodyHeader />
      <BodyContent>
        <Heading>Cannot find installation.</Heading>
        <Text fontSize="md">
          No Invoke installation found at <Strong>{installDirDetails.path}</Strong>.
        </Text>
      </BodyContent>
      <BodyFooter>
        <Button onClick={selectInstall} variant="link">
          Switch installation
        </Button>
        <Divider orientation="vertical" />
        <Button onClick={install} colorScheme="invokeYellow">
          Install
        </Button>
      </BodyFooter>
    </BodyContainer>
  );
});
LaunchFlowInvalidInstall.displayName = 'LaunchFlowInvalidInstall';
