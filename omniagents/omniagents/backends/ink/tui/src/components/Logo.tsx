/**
 * Logo component with ASCII art
 */

import React, { useMemo } from "react";
import { Box, Text } from "ink";
import { createASCIIArt, applyGradient } from "../utils/asciiArt.js";

export interface LogoProps {
  agentName: string;
}

export const Logo = React.memo<LogoProps>(({ agentName }) => {
  // Memoize ASCII art generation - only recreate when agent name changes
  const gradientLines = useMemo(() => {
    const asciiLines = createASCIIArt(agentName || "OmniAgent");
    return applyGradient(asciiLines);
  }, [agentName]);

  return (
    <Box flexDirection="column" paddingY={1}>
      {/* ASCII art logo with gradient */}
      {gradientLines.map((line, index) => (
        <Text key={index}>{line}</Text>
      ))}
    </Box>
  );
});
