/**
 * Status bar component with ASCII art header
 */

import React, { useMemo } from 'react';
import { Box, Text } from 'ink';
import { TokyoNightTheme } from '../theme.js';
import { createASCIIArt, applyGradient } from '../utils/asciiArt.js';

export interface StatusBarProps {
  agentName: string;
  connected: boolean;
  sessionId?: string;
}

export const StatusBar = React.memo<StatusBarProps>(({ agentName, connected, sessionId }) => {
  // Memoize ASCII art generation - only recreate when agent name changes
  const gradientLines = useMemo(() => {
    const asciiLines = createASCIIArt(agentName || 'OmniAgent');
    return applyGradient(asciiLines);
  }, [agentName]);

  return (
    <Box flexDirection="column" paddingY={1}>
      {/* ASCII art header with gradient */}
      {gradientLines.map((line, index) => (
        <Text key={index}>{line}</Text>
      ))}

      {/* Session ID */}
      {sessionId && (
        <Box marginTop={1}>
          <Text color={TokyoNightTheme.sessionSubtitle} italic>
            Session: {sessionId.slice(0, 8)}
          </Text>
        </Box>
      )}
    </Box>
  );
});
