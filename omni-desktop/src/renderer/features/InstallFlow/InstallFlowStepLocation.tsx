import { Button, ButtonGroup, Divider, Heading, Text } from '@invoke-ai/ui-library';
import { useStore } from '@nanostores/react';
import { memo } from 'react';
import { PiFolderOpenBold } from 'react-icons/pi';
import type { Equals } from 'tsafe';
import { assert } from 'tsafe';

import { ButtonWithTruncatedLabel } from '@/renderer/common/ButtonWithTruncatedLabel';
import { BodyContainer, BodyContent, BodyFooter, BodyHeader } from '@/renderer/common/layout';
import { Strong } from '@/renderer/common/Strong';
import { InstallFlowStepper } from '@/renderer/features/InstallFlow/InstallFlowStepper';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';
import { emitter } from '@/renderer/services/ipc';

const selectInstallDir = async () => {
  const { dirDetails } = installFlowApi.$choices.get();
  const dir = await emitter.invoke('util:select-directory', dirDetails?.path);
  if (!dir) {
    return;
  }
  const details = await emitter.invoke('util:get-dir-details', dir);
  installFlowApi.$choices.setKey('dirDetails', details);
};

export const InstallFlowStepLocation = memo(() => {
  const { dirDetails } = useStore(installFlowApi.$choices);

  return (
    <BodyContainer>
      <BodyHeader>
        <InstallFlowStepper />
      </BodyHeader>
      <BodyContent>
        <StepHeading />
        <ButtonGroup isAttached={false}>
          <ButtonWithTruncatedLabel variant="ghost" onClick={selectInstallDir} rightIcon={<PiFolderOpenBold />}>
            {dirDetails?.path ?? 'Choose install location'}
          </ButtonWithTruncatedLabel>
        </ButtonGroup>
        {dirDetails && dirDetails.canInstall && dirDetails.isInstalled && (
          <Text fontSize="md">
            When reinstalling or updating, <Strong>your data will be retained.</Strong>
          </Text>
        )}
        {dirDetails && dirDetails.canInstall && !dirDetails.isInstalled && (
          <Text fontSize="md">If there is a broken install at this location, we will reinstall it.</Text>
        )}
        {dirDetails && !dirDetails.canInstall && !dirDetails.isDirectory && (
          <Text fontSize="md">This isn&apos;t a directory.</Text>
        )}
      </BodyContent>
      <BodyFooter>
        <Button onClick={installFlowApi.cancelFlow} variant="link">
          Cancel
        </Button>
        <Divider orientation="vertical" />
        <Button
          onClick={installFlowApi.nextStep}
          isDisabled={!dirDetails || !dirDetails.canInstall}
          colorScheme="invokeYellow"
        >
          Next
        </Button>
      </BodyFooter>
    </BodyContainer>
  );
});
InstallFlowStepLocation.displayName = 'InstallFlowStepLocation';

const StepHeading = memo(() => {
  const { dirDetails } = useStore(installFlowApi.$choices);

  if (!dirDetails) {
    return <Heading>Where should we install Invoke?</Heading>;
  }
  if (!dirDetails.canInstall) {
    return <Heading>Invalid install location.</Heading>;
  }
  if (dirDetails.canInstall && !dirDetails.isInstalled) {
    return <Heading>Fresh install.</Heading>;
  }
  if (dirDetails.canInstall && dirDetails.isInstalled) {
    return <Heading>Existing Invoke {dirDetails.version} install found.</Heading>;
  }
  assert<Equals<typeof dirDetails, never>>(dirDetails, 'This should never happen');
});
StepHeading.displayName = 'StepHeading';
