/**
 * Preamble component - shows agent's current reasoning/message during a run
 * This is intermediate content that may be superseded by tool calls
 */

import React, { useState, useEffect } from "react";
import { Box, Text } from "ink";
import { TokyoNightTheme } from "../theme.js";

// Star spinner frames (from ink-spinner)
const STAR_FRAMES = ["✶", "✸", "✹", "✺", "✹", "✸"];

export interface PreambleProps {
  content: string;
}

export const Preamble: React.FC<PreambleProps> = ({ content }) => {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    // Slower animation: 200ms per frame (default is ~80ms)
    const interval = setInterval(() => {
      setFrame((prev) => (prev + 1) % STAR_FRAMES.length);
    }, 200);
    return () => clearInterval(interval);
  }, []);

  return (
    <Box flexDirection="column" marginTop={1}>
      <Box>
        <Box marginRight={1}>
          <Text color={TokyoNightTheme.warning}>{STAR_FRAMES[frame]}</Text>
        </Box>
        <Text color={TokyoNightTheme.warning}>{content}</Text>
      </Box>
    </Box>
  );
};
