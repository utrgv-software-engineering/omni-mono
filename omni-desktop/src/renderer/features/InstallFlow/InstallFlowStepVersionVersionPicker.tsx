import { Button, ButtonGroup, Heading, Text } from '@invoke-ai/ui-library';
import { useStore } from '@nanostores/react';
import { memo, useCallback } from 'react';

import { EllipsisLoadingText } from '@/renderer/common/EllipsisLoadingText';
import { InstallFlowInstallTypeDescription } from '@/renderer/features/InstallFlow/InstallFlowInstallTypeDescription';
import { installFlowApi } from '@/renderer/features/InstallFlow/state';
import { $latestGHReleases, syncGHReleases } from '@/renderer/services/gh';

export const InstallFlowStepVersionVersionPicker = memo(() => {
  return (
    <>
      <Heading>Which version should we install?</Heading>
      <VersionPicker />
    </>
  );
});
InstallFlowStepVersionVersionPicker.displayName = 'InstallFlowStepVersionVersionPicker';

const VersionPicker = memo(() => {
  const latestGHReleases = useStore($latestGHReleases);
  const { release } = useStore(installFlowApi.$choices);
  const installType = useStore(installFlowApi.$installType);

  if (latestGHReleases.isError) {
    return (
      <Text role="button" onClick={syncGHReleases} fontSize="md" color="error.300" fontWeight="semibold">
        Unable to get available releases from GitHub. Click here to retry.
      </Text>
    );
  }

  if (latestGHReleases.isLoading || latestGHReleases.isUninitialized || !release) {
    return (
      <EllipsisLoadingText fontSize="md" color="base.300" fontWeight="semibold">
        Loading releases
      </EllipsisLoadingText>
    );
  }

  return (
    <>
      <ButtonGroup variant="outline">
        <VersionButton version={latestGHReleases.data.stable} isPrerelease={false} />
        {latestGHReleases.data.pre && <VersionButton version={latestGHReleases.data.pre} isPrerelease={true} />}
      </ButtonGroup>
      {installType && <InstallFlowInstallTypeDescription installType={installType} />}
    </>
  );
});
VersionPicker.displayName = 'VersionPicker';

const VersionButton = memo(({ version, isPrerelease }: { version: string; isPrerelease: boolean }) => {
  const selectedRelease = useStore(installFlowApi.$choices).release;
  const onClick = useCallback(() => {
    installFlowApi.$choices.setKey('release', { version, isPrerelease });
  }, [isPrerelease, version]);

  return (
    <Button onClick={onClick} colorScheme={selectedRelease?.version === version ? 'invokeBlue' : 'base'}>
      {isPrerelease ? 'Prerelease' : 'Stable'} ({version})
    </Button>
  );
});
VersionButton.displayName = 'VersionButton';
