import React from "react";
import { Box, Text } from "ink";
import { useRecap } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

// Docked session-recap panel (from /recap or the idle auto-recap).
// Passive — renders the latest recap prose until a new run starts (which
// clears it via setRecap(null) in useMessagesNew's run_started handler).
// Renders nothing when no recap is set. The enclosing ScrollableMessageList
// handles overflow for a long (~400-word) block.
export const Recap = React.memo(() => {
  const recap = useRecap();
  if (!recap) return null;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.toolResult}
    >
      <Box>
        <Text color={TokyoNightTheme.defaultText}>Session recap</Text>
      </Box>
      <Box marginTop={1}>
        <Text color={TokyoNightTheme.inputText}>{recap.text.trim()}</Text>
      </Box>
    </Box>
  );
});
