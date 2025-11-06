/**
 * Component to display agent activity status
 */

import React from 'react';
import { Box, Text } from 'ink';
import Spinner from 'ink-spinner';
import { TokyoNightTheme } from '../theme.js';

export interface ToolActivityProps {
  isThinking: boolean;
  customStatus?: string | null;
}

// Isolate spinner in its own component to prevent parent re-renders
const SpinnerIcon = React.memo(() => <Spinner type="dots" />);

export const ToolActivity: React.FC<ToolActivityProps> = React.memo(({ isThinking, customStatus }) => {
  if (!isThinking) {
    return null;
  }

  return (
    <Box flexDirection="column" paddingY={1}>
      <Box>
        <Text color={TokyoNightTheme.info}>
          <SpinnerIcon />
        </Text>
        <Text dimColor> {customStatus || 'Thinking...'}</Text>
      </Box>
    </Box>
  );
}, (prev, next) => {
  // Only re-render if isThinking or customStatus changes
  return prev.isThinking === next.isThinking &&
         prev.customStatus === next.customStatus;
});
