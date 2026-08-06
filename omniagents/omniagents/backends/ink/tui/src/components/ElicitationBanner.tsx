import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useElicitation } from "../store/appStore.js";
import { Theme } from "../theme.js";

// Banner shown for ``question``-kind elicitations (structured user
// elicitation, protocol.md § "User Elicitation Flow"). Free-text answers
// reuse the escalation idiom: the next message the user submits is
// routed back as ``elicitation_response(action="accept", {text})`` by
// the App.tsx submit interceptor. Non-question kinds render as the
// ElicitationDialog instead; renders nothing when no question is
// pending.
export const ElicitationBanner = React.memo(() => {
  const elicitation = useElicitation();
  if (!elicitation || elicitation.kind !== "question") return null;

  return (
    <Box
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.stateActive}
      flexDirection="column"
    >
      <Box>
        <Text color={Theme.stateBlocked}>
          <Spinner type="dots" />
        </Text>
        <Text color={Theme.stateBlocked} bold>
          {" "}
          {elicitation.title || "The agent has a question"} — your next
          message will be sent as the answer.
        </Text>
      </Box>
      <Box>
        <Text color={Theme.textChrome}>{elicitation.message}</Text>
      </Box>
      {elicitation.expires_at ? (
        <Box>
          <Text color={Theme.textSecondary}>
            Expires {elicitation.expires_at}
          </Text>
        </Box>
      ) : null}
    </Box>
  );
});
