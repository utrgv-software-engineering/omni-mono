/**
 * Header component containing Logo and Welcome message
 */

import React from "react";
import { Box, Text } from "ink";
import { Logo } from "./Logo.js";
import { TokyoNightTheme } from "../theme.js";

export interface HeaderProps {
  agentName: string;
  welcomeText?: string;
  visible: boolean;
}

export const Header = React.memo<HeaderProps>(
  ({ agentName, welcomeText, visible }) => {
    return (
      <Box
        flexDirection="column"
        paddingX={1}
        display={visible ? "flex" : "none"}
      >
        {/* Logo */}
        <Logo agentName={agentName} />

        {/* Welcome message */}
        {welcomeText && (
          <Box paddingY={1} marginTop={1}>
            <Text color={TokyoNightTheme.defaultText}>{welcomeText}</Text>
          </Box>
        )}
      </Box>
    );
  },
  (prev, next) => {
    // Only re-render if props actually changed
    return (
      prev.agentName === next.agentName &&
      prev.welcomeText === next.welcomeText &&
      prev.visible === next.visible
    );
  },
);
