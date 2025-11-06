/**
 * Reduced-motion tool activity component
 * Avoids high-frequency re-renders to keep the input stable
 */

import React from 'react';
import { Box, Text } from 'ink';
import { useThinking, useStatus } from '../store/appStore.js';
import { TokyoNightTheme } from '../theme.js';

export const ToolActivityOptimized = React.memo(() => {
  const thinking = useThinking();
  const status = useStatus();

  if (!thinking && !status) {
    return null;
  }

  return (
    <Box paddingY={1}>
      <Text color={TokyoNightTheme.info}>{'● '}</Text>
      <Text dimColor>{status || 'Thinking...'} </Text>
    </Box>
  );
});
