import { Button, Divider, Heading, Text } from '@invoke-ai/ui-library';
import { memo, useCallback } from 'react';

import { BodyContainer, BodyContent, BodyFooter, BodyHeader } from '@/renderer/common/layout';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';
import { useSelectInstall } from '@/renderer/hooks/use-select-install';

export const FirstRun = memo(() => {
  const selectInstall = useSelectInstall();
  const install = useCallback(() => {
    installFlowApi.beginFlow();
  }, []);

  return (
    <BodyContainer>
      <BodyHeader />
      <BodyContent>
        <Heading>Welcome to Invoke.</Heading>
        <Text fontSize="md">Install or select an existing installation to manage.</Text>
      </BodyContent>
      <BodyFooter>
        <Button onClick={selectInstall} variant="link">
          Select an existing installation
        </Button>
        <Divider orientation="vertical" />
        <Button onClick={install} colorScheme="invokeYellow">
          Install
        </Button>
      </BodyFooter>
    </BodyContainer>
  );
});
FirstRun.displayName = 'FirstRun';
