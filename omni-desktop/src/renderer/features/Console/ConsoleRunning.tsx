import { Box, Divider, Flex, IconButton, Spacer, Text } from '@invoke-ai/ui-library';
import { memo } from 'react';
import { PiArrowCounterClockwiseBold, PiCaretDownBold, PiXBold } from 'react-icons/pi';

import { ConsoleXterm } from '@/renderer/features/Console/ConsoleXterm';
import { $isConsoleOpen, destroyTerminal, type TerminalState } from '@/renderer/features/Console/state';
import { useNewTerminal } from '@/renderer/features/Console/use-new-terminal';

type Props = {
  terminal: TerminalState;
};

const closeConsole = () => {
  $isConsoleOpen.set(false);
};

export const ConsoleStarted = memo(({ terminal }: Props) => {
  const newTerminal = useNewTerminal();
  return (
    <Flex w="full" h="full" position="relative" flexDir="column" minH={0}>
      <Flex w="full" h={10} alignItems="center" px={2}>
        <IconButton
          aria-label="Kill Console"
          onClick={destroyTerminal}
          size="sm"
          variant="link"
          alignSelf="stretch"
          icon={<PiXBold />}
          colorScheme="error"
        />
        <Spacer />
        <Text color="base.500" userSelect="none">
          Dev Console
        </Text>
        <Spacer />
        <IconButton
          aria-label="Restart Console"
          onClick={newTerminal}
          size="sm"
          variant="link"
          alignSelf="stretch"
          icon={<PiArrowCounterClockwiseBold />}
        />
        <IconButton
          aria-label="Hide Console"
          onClick={closeConsole}
          size="sm"
          variant="link"
          alignSelf="stretch"
          icon={<PiCaretDownBold />}
        />
      </Flex>
      <Divider />
      <Box w="full" h="full" p={2} minH={0}>
        <ConsoleXterm terminal={terminal} />
      </Box>
    </Flex>
  );
});
ConsoleStarted.displayName = 'ConsoleStarted';
