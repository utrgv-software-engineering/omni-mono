import type { SystemStyleObject } from '@invoke-ai/ui-library';
import { Box, Flex } from '@invoke-ai/ui-library';
import { useStore } from '@nanostores/react';
import type { MouseEvent } from 'react';
import { memo } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';

import { ConsoleNotRunning } from '@/renderer/features/Console/ConsoleNotStarted';
import { ConsoleStarted } from '@/renderer/features/Console/ConsoleRunning';
import { $isConsoleOpen, $terminal } from '@/renderer/features/Console/state';

const sx: SystemStyleObject = {
  transition: 'all .07s ease',
  '&[data-open="false"]': {
    transition: 'all .2s ease',
    transform: 'scale(0.9)',
    opacity: 0,
    pointerEvents: 'none',
  },
};

const onClose = () => {
  $isConsoleOpen.set(false);
};

const onClickOverlay = (e: MouseEvent) => {
  if (e.target !== e.currentTarget) {
    e.stopPropagation();
    return;
  }
  onClose();
};

export const Console = memo(() => {
  const isOpen = useStore($isConsoleOpen);
  const terminal = useStore($terminal);

  useHotkeys('esc', onClose);

  return (
    <Box onClick={onClickOverlay} position="absolute" inset={0} data-open={isOpen} sx={sx}>
      <Flex
        position="absolute"
        inset={4}
        insetBlockEnd="calc(var(--invoke-space-20) - var(--invoke-space-2))"
        borderRadius="base"
        borderWidth="1px"
        borderColor="whiteAlpha.50"
        overflow="hidden"
        padding="inherit"
        backdropFilter="blur(32px)"
        shadow="dark-lg"
      >
        <Box position="absolute" inset={0} bg="base.900" opacity={0.7} />
        <Box position="relative" w="full" h="full" minH={0}>
          {!terminal && <ConsoleNotRunning />}
          {terminal && <ConsoleStarted terminal={terminal} />}
        </Box>
      </Flex>
    </Box>
  );
});
Console.displayName = 'Console';
