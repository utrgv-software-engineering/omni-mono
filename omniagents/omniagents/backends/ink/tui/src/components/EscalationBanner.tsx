import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useEscalation } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

// Banner shown when the agent calls ``escalate`` (the blocking
// client-function builtin). The agent is paused on the tool call
// awaiting our ``client_response``; the next message the user submits
// is routed back as the reply via the App.tsx submit interceptor.
// Renders nothing when no escalation is pending.
export const EscalationBanner = React.memo(() => {
  const escalation = useEscalation();
  if (!escalation) return null;

  return (
    <Box
      borderStyle="round"
      borderColor={TokyoNightTheme.warning}
      flexDirection="column"
    >
      <Box>
        <Text color={TokyoNightTheme.warning}>
          <Spinner type="dots" />
        </Text>
        <Text color={TokyoNightTheme.warning} bold>
          {" "}
          Agent escalated — your next message will be sent back as the reply.
        </Text>
      </Box>
      <Box>
        <Text color={TokyoNightTheme.defaultText}>{escalation.message}</Text>
      </Box>
    </Box>
  );
});
