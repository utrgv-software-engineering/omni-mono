import { Text } from '@invoke-ai/ui-library';
import { memo } from 'react';

import { Strong } from '@/renderer/common/Strong';
import type { InstallType } from '@/shared/types';

type Props = {
  installType: InstallType;
};

export const InstallFlowInstallTypeDescription = memo(({ installType }: Props) => {
  if (installType.type === 'fresh') {
    return (
      <Text fontSize="md">
        We&apos;ll install <Strong>Invoke {installType.newVersion}</Strong>.
      </Text>
    );
  }
  if (installType.type === 'reinstall') {
    return (
      <Text fontSize="md">
        We&apos;ll reinstall your existing <Strong>Invoke {installType.installedVersion}</Strong> install.
      </Text>
    );
  }
  if (installType.type === 'upgrade') {
    return (
      <Text fontSize="md">
        We&apos;ll upgrade your existing <Strong>Invoke {installType.installedVersion}</Strong> install to{' '}
        <Strong>{installType.newVersion}</Strong>.
      </Text>
    );
  }
  if (installType.type === 'downgrade') {
    return (
      <Text fontSize="md">
        We&apos;ll downgrade your existing <Strong>Invoke {installType.installedVersion}</Strong> install to{' '}
        <Strong>{installType.newVersion}</Strong>.
      </Text>
    );
  }
});

InstallFlowInstallTypeDescription.displayName = 'InstallFlowInstallTypeDescription';
