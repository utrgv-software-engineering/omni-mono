import React from "react";
import { Box, Text } from "ink";
import { Theme } from "../../theme.js";

// The design system's empty state: centered in the space the content
// would occupy, LazyVim-dashboard style. Explains what the screen is,
// what will make content appear, and which keys matter — an empty
// screen is a teaching moment, not a void.
export const EmptyState = React.memo<{
  title: string;
  lines?: string[];
  actions?: Array<{ keys: string; label: string }>;
}>(({ title, lines = [], actions = [] }) => (
  <Box
    flexDirection="column"
    flexGrow={1}
    width="100%"
    justifyContent="center"
    alignItems="center"
  >
    <Text color={Theme.textPrimary} bold>
      {title}
    </Text>
    {lines.length > 0 && <Box height={1} />}
    {lines.map((line, index) => (
      <Text key={index} color={Theme.textChrome}>
        {line}
      </Text>
    ))}
    {actions.length > 0 && <Box height={1} />}
    <Box flexDirection="column">
      {actions.map((action) => (
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
));
