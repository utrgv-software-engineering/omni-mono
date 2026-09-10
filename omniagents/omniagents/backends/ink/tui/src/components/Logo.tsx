/**
 * The dashboard logo: gradient ASCII art, LazyVim-style — the empty
 * state's centerpiece. It only ever renders on a fresh session (Header
 * gates on messages.length === 0 && chat screen), so it costs zero rows
 * once work starts.
 */

import React, { useMemo } from "react";
import { Box, Text } from "ink";
import { createASCIIArt, applyGradient } from "../utils/asciiArt.js";
import { themedMemo } from "../theme/themed-memo.js";

export interface LogoProps {
  agentName: string;
}

export const Logo = themedMemo<LogoProps>(({ agentName }) => {
  // Memoize ASCII art generation - only recreate when agent name changes
  const gradientLines = useMemo(() => {
    const asciiLines = createASCIIArt(agentName || "OmniAgent");
    return applyGradient(asciiLines);
  }, [agentName]);

  return (
    <Box flexDirection="column" paddingY={1} alignItems="center">
      {gradientLines.map((line, index) => (
        <Text key={index}>{line}</Text>
      ))}
    </Box>
  );
});
