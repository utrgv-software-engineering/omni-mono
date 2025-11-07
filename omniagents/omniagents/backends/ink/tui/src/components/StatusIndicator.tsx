import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useThinking, useStatus } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

// Animated spinner with status text
export const StatusIndicator = React.memo(() => {
  const thinking = useThinking();
  const status = useStatus();

  if (!thinking && !status) {
    return null;
  }

  return (
    <Box>
      <Text>
        <Text color={TokyoNightTheme.info}>
          <Spinner type="dots" />
        </Text>{" "}
        <Text dimColor>{status || "Thinking..."}</Text>
      </Text>
    </Box>
  );
});
