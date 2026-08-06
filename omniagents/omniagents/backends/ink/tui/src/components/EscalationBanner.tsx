import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useEscalation } from "../store/appStore.js";
import { Theme } from "../theme.js";

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
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.stateBlocked}
      flexDirection="column"
    >
      <Box>
        <Text color={Theme.stateBlocked}>
          <Spinner type="dots" />
        </Text>
        <Text color={Theme.stateBlocked} bold>
          {" "}
          Agent escalated — your next message will be sent back as the reply.
        </Text>
      </Box>
      <Box>
        <Text color={Theme.textChrome}>{escalation.message}</Text>
      </Box>
    </Box>
  );
});
