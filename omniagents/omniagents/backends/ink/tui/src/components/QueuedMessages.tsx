import React from "react";
import { Box, Text } from "ink";
import { useQueuedMessages } from "../store/appStore.js";
import { Theme } from "../theme.js";

const MAX_VISIBLE = 5;

/**
 * "Up next" dock rendered just above the prompt. Mirrors the launcher /
 * web-UI panel: lists user messages that have been enqueued while a run
 * is active, with order preserved. Returns null when empty so the
 * message area reclaims the space.
 *
 * No keybindings here — cancellation lives on the server side via
 * cancel_queued_message and is wired up from App.tsx when we get
 * around to in-place panel controls. For now this is read-only display.
 */
export const QueuedMessages = React.memo<{ focused: boolean }>(
  ({ focused }) => {
    const items = useQueuedMessages();
    if (!focused) return null;
    if (!items || items.length === 0) {
      return (
        <Box paddingX={1} borderStyle="round" borderColor={Theme.borderAmbient}>
          <Text color={Theme.textChrome}>queue is empty</Text>
        </Box>
      );
    }

  const visible = items.slice(0, MAX_VISIBLE);
  const overflow = items.length - visible.length;

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.borderAmbient}
    >
      <Box>
        <Text color={Theme.textChrome}>Up next </Text>
        <Text color={Theme.stateActive}>{items.length}</Text>
        <Text color={Theme.textChrome}> queued</Text>
      </Box>
      {visible.map((item, idx) => (
        <Box key={item.id}>
          <Text color={Theme.textChrome}>{idx + 1}. </Text>
          <Text color={Theme.textPrimary}>
            {item.content.length > 80
              ? `${item.content.slice(0, 77)}…`
              : item.content}
          </Text>
        </Box>
      ))}
      {overflow > 0 && (
        <Text color={Theme.textChrome}>… +{overflow} more</Text>
      )}
    </Box>
  );
});
