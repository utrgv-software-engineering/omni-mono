import { Button, Flex, Text } from '@invoke-ai/ui-library';
import { useStore } from '@nanostores/react';
import { memo } from 'react';

import { Strong } from '@/renderer/common/Strong';
import { useNewTerminal } from '@/renderer/features/Console/use-new-terminal';
import { $installDirDetails } from '@/renderer/services/store';

export const ConsoleNotRunning = memo(() => {
  const installDir = useStore($installDirDetails);
  const newTerminal = useNewTerminal();
  return (
    <Flex position="relative" flexDir="column" w="full" h="full" alignItems="center" justifyContent="center" gap={4}>
      <Button variant="link" onClick={newTerminal}>
        Start Dev Console
      </Button>
      {installDir?.isInstalled && (
        <Text fontSize="md">
          We&apos;ll activate the virtual environment for the install at <Strong>{installDir.path}</Strong>.
        </Text>
      )}
    </Flex>
  );
});
ConsoleNotRunning.displayName = 'ConsoleNotRunning';
