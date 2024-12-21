import { Button, Divider, Heading, Text } from '@invoke-ai/ui-library';
import { memo, useCallback } from 'react';

import { BodyContainer, BodyContent, BodyFooter, BodyHeader } from '@/renderer/common/layout';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';
import { selectInstallDir } from '@/renderer/services/store';

export const FirstRun = memo(() => {
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
        <Button onClick={selectInstallDir} variant="link">
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
