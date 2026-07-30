import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useElicitation } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

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
      borderStyle="round"
      borderColor={TokyoNightTheme.info}
      flexDirection="column"
    >
      <Box>
        <Text color={TokyoNightTheme.warning}>
          <Spinner type="dots" />
        </Text>
        <Text color={TokyoNightTheme.warning} bold>
          {" "}
          {elicitation.title || "The agent has a question"} — your next
          message will be sent as the answer.
        </Text>
      </Box>
      <Box>
        <Text color={TokyoNightTheme.defaultText}>{elicitation.message}</Text>
      </Box>
      {elicitation.expires_at ? (
        <Box>
          <Text color={TokyoNightTheme.toolResult}>
            Expires {elicitation.expires_at}
          </Text>
        </Box>
      ) : null}
    </Box>
  );
});
