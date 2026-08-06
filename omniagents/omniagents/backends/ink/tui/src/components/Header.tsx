/**
 * Header component containing Logo and Welcome message
 */

import React from "react";
import { Box, Text } from "ink";
import { Logo } from "./Logo.js";
import { Theme } from "../theme.js";

export interface HeaderProps {
  agentName: string;
  welcomeText?: string;
  visible: boolean;
}

// The fresh-session dashboard (LazyVim-style): centered logo, welcome
// line, and a small keyed action list. Disappears entirely once the
// first message lands — the dashboard is the empty state, not chrome.
const DASHBOARD_ACTIONS: Array<{ keys: string; label: string }> = [
  { keys: "type", label: "start a conversation" },
  { keys: "tab", label: "review · agents" },
  { keys: "ctrl+a", label: "panels" },
  { keys: "/model", label: "switch model" },
];

export const Header = React.memo<HeaderProps>(
  ({ agentName, welcomeText, visible }) => {
    return (
      <Box
        flexDirection="column"
        paddingX={1}
        display={visible ? "flex" : "none"}
        alignItems="center"
      >
        {/* Logo */}
        <Logo agentName={agentName} />

        {/* Welcome message */}
        {welcomeText && (
          <Box paddingY={1}>
            <Text color={Theme.textChrome}>{welcomeText}</Text>
          </Box>
        )}

        {/* Keyed actions, LazyVim dashboard style */}
        <Box flexDirection="column">
          {DASHBOARD_ACTIONS.map((action) => (
            <Box key={action.keys}>
              <Box width={10}>
                <Text color={Theme.accentUser} bold>
                  {action.keys}
                </Text>
              </Box>
              <Text color={Theme.textChrome}>{action.label}</Text>
            </Box>
          ))}
        </Box>
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
