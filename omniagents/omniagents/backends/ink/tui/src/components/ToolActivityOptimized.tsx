import React from 'react';
import { Box, Text } from 'ink';
import Spinner from 'ink-spinner';
import { useThinking, useStatus } from '../store/appStore.js';
import { TokyoNightTheme } from '../theme.js';

// Animated spinner using ink-spinner
export const ToolActivityOptimized = React.memo(() => {
  const thinking = useThinking();
  const status = useStatus();

  if (!thinking && !status) {
    return null;
  }

  return (
    <Box>
      <Text color={TokyoNightTheme.info}>
        <Spinner type="dots" />
      </Text>
      <Text> </Text>
      <Text dimColor>{status || 'Thinking...'}</Text>
    </Box>
  );
});
