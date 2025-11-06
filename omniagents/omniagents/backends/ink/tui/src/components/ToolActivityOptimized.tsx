import React, { useEffect, useState } from 'react';
import { Box, Text } from 'ink';
import { useThinking, useStatus } from '../store/appStore.js';
import { TokyoNightTheme } from '../theme.js';

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const FRAME_INTERVAL = 120;

export const ToolActivityOptimized = React.memo(() => {
  const thinking = useThinking();
  const status = useStatus();
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    if (!thinking) {
      setFrameIndex(0);
      return;
    }

    const id = setInterval(() => {
      setFrameIndex((current) => (current + 1) % SPINNER_FRAMES.length);
    }, FRAME_INTERVAL);

    return () => {
      clearInterval(id);
    };
  }, [thinking]);

  if (!thinking && !status) {
    return null;
  }

  const glyph = thinking ? SPINNER_FRAMES[frameIndex] : '●';

  return (
    <Box paddingY={1}>
      <Text color={TokyoNightTheme.info}>{glyph} </Text>
      <Text dimColor>{status || 'Thinking...'}</Text>
    </Box>
  );
});
